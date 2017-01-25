# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import ConfigParser
import os
import socket
import StringIO
import time
import traceback
import urllib2

import boto3
import botocore
from chalice import Chalice
from chalice.app import ChaliceError
import slackweb

S3_CONFIG_BUCKET_NAME = os.getenv('S3_CONFIG_BUCKET_NAME')
S3_CONFIG_KEY_NAME = os.getenv('S3_CONFIG_KEY_NAME')
SLACK_CHANNEL = os.getenv('SLACK_CHANNEL')
SLACK_USERNAME = os.getenv('SLACK_USERNAME')
SLACK_WEBHOOK = os.getenv('SLACK_WEBHOOK')


class MyChalice(Chalice):
    def __call__(self, event, context):
        try:
            return super(MyChalice, self).__call__(event, context)
        except ChaliceError:
            return check_all()

app = MyChalice(app_name='healthchecker')
app.debug = False


@app.route('/')
def index():
    ret = {}
    try:
        ini = _get_ini()
    except botocore.exceptions.ClientError:
        return {'Error': 'Cannot read ini file.'}

    for section in ini.sections():
        params = _get_section_params(ini, section)
        ret[section] = {
            'url': params['url'],
            'timeout': params['timeout'],
            'status': params['status'],
        }
    return ret


@app.route('/checkall')
def check_all():
    try:
        ini = _get_ini()
    except botocore.exceptions.ClientError:
        return {'Error': 'Cannot read ini file.'}
    for section in ini.sections():
        _check_section(ini, section)


def _notify(text, **kwargs):
    webhook_url = kwargs.pop('webhook_url', SLACK_WEBHOOK)
    params = {
        'text': text,
        'channel': SLACK_CHANNEL,
        'user': SLACK_USERNAME,
    }
    params.update(kwargs)

    slack = slackweb.Slack(url=webhook_url)
    slack.notify(**params)


def _get_config_file():
    s3 = boto3.client('s3')
    out = StringIO.StringIO()
    s3.download_fileobj(S3_CONFIG_BUCKET_NAME, S3_CONFIG_KEY_NAME, out)
    out.seek(0, 0)
    return out


def _get_ini():
    ini = ConfigParser.SafeConfigParser()
    ini.readfp(_get_config_file(), filename=S3_CONFIG_KEY_NAME)
    return ini


def _get_section_params(ini, section):
    return {
        'url': ini.get(section, 'url'),
        'timeout': ini.getfloat(section, 'timeout'),
        'status': ini.getint(section, 'status'),
        'slack_channel': ini.get(section, 'slack_channel'),
        'slack_username': ini.get(section, 'slack_username'),
        'slack_webhook': ini.get(section, 'slack_webhook'),
    }


def _check_section(ini, section):
    params = _get_section_params(ini, section)

    actual_timeout = max(params['timeout'] * 2, 10)

    error_message = None
    error_emoji = ':cold_sweat:'

    _start = time.time()
    req = urllib2.Request(params['url'])
    try:
        res = urllib2.urlopen(req, timeout=actual_timeout)
    except urllib2.HTTPError, e:
        actual_status = int(e.code)
    except urllib2.URLError, e:
        error_message = 'URLErrorが発生しました。'
        error_emoji = ':boom:'
    except socket.timeout:
        error_message = 'サーバーが{}秒間レスポンスを返せずタイムアウトしました。'
    except Exception:
        error_message = '\n'.join([
            '不明なエラーです。',
            '```',
            '{}',
            '```',
        ]).format(traceback.format_exc().rstrip('\n'))
    else:
        actual_status = int(res.code)
    _end = time.time()

    if not error_message:
        if params['status'] != actual_status:
            if actual_status == 503:
                error_message = 'メンテナンス中です。'
                error_emoji = ':ambulance:'
            else:
                error_message = 'ステータスコードが{}ではなく{}でした。'.format(
                    params['status'], actual_status,
                )
        elif params['timeout'] <= _end - _start:
            error_message = (
                'サーバーが重く危険な状態です。'
                '{:.3f}秒以内にレスポンスを返すべきですが{:.3f}秒かかりました。'
            ).format(params['timeout'], _end - _start)

    if error_message:
        _notify(
            '{} [{}] {}\n{}'.format(error_emoji, section, params['url'], error_message),
            channel=params['slack_channel'],
            username=params['slack_username'],
            webhook_url=params['slack_webhook'],
        )
