#!/usr/bin/env python
# Description	:	This script will check daily instahms upgrade status and report to slack 
#                       channel #XXXXXXXXX if there is any failure/delay in upgrade.

import os
import sys
import json
import logging
import requests
import datetime
import subprocess
from os import environ, path

UPGRADE_LIST = "/opt/custom_scripts/upgrade_list.txt"
PLAY_PATH = "/home/sreejith.vu/upgrade_status_ansible/"
WEBHOOK_URL = 'XXXXXXXXXXXXXXXXXXXXXX'

logFormatter = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(filename="/tmp/upgrade/instahms_upgrade.log", format=logFormatter)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def to_slack(playbook_vars, msg):
    try:
        server = playbook_vars["server"]
        upgrade_date = playbook_vars["upgrade_date"]
        DATA = {
            'channel': '#XXXXXXXXXXX',
            'text': '```ERROR : Server : '+server+' : '+msg+'```',
            'username': 'InstaHMS ['+upgrade_date+'] Upgrade Status',
            'icon_emoji': ':skull_and_crossbones:'
        }
        requests.post(WEBHOOK_URL, data=json.dumps(
            DATA), headers={'Content-Type': 'application/json'})
        logger.info("Sending msg to Slack")
    except Exception as e:
        logger.error("Error in sending msg to slack", e)


def ansible_playbook(playbook, playbook_vars, logfile=None):
    """ Executes an ansible playbook
    params:
      playbook      - (String) Name of the file to execute under ansible
                        playbook directory
      extra_vars    - (Dict) Additional vars required to run ansible source
                        e.g: {"hosts": "testsvr2", "mode": "http"}
      logfile       - (String) Path of file where the playbook's output will be
                        logged
    """
    env = environ.copy()
    if logfile is not None:
        env['ANSIBLE_LOG_PATH'] = logfile
    env['ANSIBLE_STDOUT_CALLBACK'] = 'unixy'

    cmd = " ".join([
        "ansible-playbook",
        path.join(playbook),
        "--extra-vars '{0}'".format(json.dumps(playbook_vars)),
        "-v"
    ])
    subprocess.check_output(cmd, env=env, shell=True)


def alert_status(playbook_vars, msg):
    try:
        ansible_playbook(PLAY_PATH+'check_alerted.yml', playbook_vars)
        logger.info("Already alerted to slack")
    except Exception as e:
        logger.info("Sending alert to slack")
        ansible_playbook(PLAY_PATH+'alerting.yml', playbook_vars)
        to_slack(playbook_vars, msg)

def check_time_limit(playbook_vars, version):
    if version == "minor":
        try:
            playbook_vars.update({"time_limit": 2})
            ansible_playbook(PLAY_PATH+'time_check.yml', playbook_vars)
            logger.info("Minor upgrade is within time limit of 2 hours")
        except Exception as e:
            logger.error(
                "Minor upgrade crossed time limit of 2 hours", e)
            playbook_vars.update({"alert": "time_limit_error" })
            msg = "Minor upgrade crossed time limit of 2 hours"
            alert_status(playbook_vars, msg)

    if version == "major":
        try:
            playbook_vars.update({"time_limit": 4})
            ansible_playbook(PLAY_PATH+'time_check.yml', playbook_vars)
            logger.info("Major upgrade is within time limit of 4 hours")
        except Exception as e:
            logger.error(
                "Major upgrade crossed time limit of 4 hours")
            playbook_vars.update({"alert": "time_limit_error" })
            msg = "Major upgrade crossed time limit of 4 hours"
            alert_status(playbook_vars, msg)


def get_status(playbook_vars):
    try:
        ansible_playbook(PLAY_PATH+'check_upgrade_began.yml', playbook_vars)
        logger.info("Upgrade started")
    except Exception as e:
        logger.info("Upgrade didn't started")
        return

    try:
        ansible_playbook(PLAY_PATH+'check_upgrade_ended.yml', playbook_vars)
        logger.info("Upgrade finished")
        if playbook_vars['server'].startswith('apps'):
            try:
                playbook_vars.update({"alert": "liquibase_error" })
                ansible_playbook(PLAY_PATH+'apps_check_and_alert_liquibase_err.yml', playbook_vars)
                logger.info("Checked for liquibase errors in apps server")
            except Exception as e:
                logger.info("Error in running apps_check_and_alert_liquibase_err.yml")
        else:
            try:
                playbook_vars.update({"alert": "liquibase_error" })
                ansible_playbook(PLAY_PATH+'check_and_alert_liquibase_err.yml', playbook_vars)
                logger.info("Checked for liquibase errors")
            except Exception as e:
                logger.error("Error in running check_and_alert_liquibase_err.yml", e)
        try:
            ansible_playbook(PLAY_PATH+'check_login_page.yml', playbook_vars)
            logger.info("Login page is loading fine")
        except Exception as e:
            logger.error("Error in loading login page")
            playbook_vars.update({"alert": "login_error" })
            msg = "Error in loading login page"
            alert_status(playbook_vars, msg)

    except Exception as e:
        logger.warning("Upgrade didn't completed")

        try:
            ansible_playbook(PLAY_PATH+'check_minor.yml', playbook_vars)
            logger.info("Minor version upgrade")
            version = "minor"
            check_time_limit(playbook_vars, version)
        except Exception as e:
            logger.info("Major version upgrade")
            version = "major"
            check_time_limit(playbook_vars, version)


def main():
    try:
        time = str(datetime.datetime.now()).split(" ")[1].split(":", 1)[0]
        if time == "08":
            os.system("> " + UPGRADE_LIST)
            logger.info("Clearing schemas from upgrade list - %s", UPGRADE_LIST)
            sys.exit(2)        
        logger.info("Not clearing upgrade list - %s", UPGRADE_LIST)
    except Exception as e:
        logger.error("Error in removing %s" % UPGRADE_LIST)
        sys.exit(2)        

    try:
        if os.stat(UPGRADE_LIST).st_size != 0:
            with open(UPGRADE_LIST) as file:
                lines = file.readlines()
                logger.info("Reading from %s", UPGRADE_LIST)
        else:
            logger.info("Found empty file %s, Nothing to check for upgrade.", UPGRADE_LIST)
            sys.exit(2)
    except Exception as e:
        logger.error("%s not found, exiting." % UPGRADE_LIST)
        sys.exit(2)

    try:
        for line in lines:
            hostname = str(line.split(" ", 1)[0])
            proposed_version = str(line.split(" ", 1)[1].strip('\n'))
            logger.info("Customer/Schema is : %s", hostname)
            logger.info("Proposed version is : %s", proposed_version)
            upgrade_date = str(datetime.datetime.now()).split(" ", 1)[0]
            liquibase_date = str(datetime.datetime.now().strftime("%d/%m/%Y"))

            if hostname.endswith('app') and not hostname.startswith('apps'):
                schema_name = hostname[:-3]
            else:
                schema_name = hostname

            playbook_vars = {
                "server": hostname,
                "schema": schema_name,
                "upgrade_date": upgrade_date,
                "liquibase_date": liquibase_date,
                "proposed_version": proposed_version
            }
            get_status(playbook_vars)
    except Exception as e:
        logger.error(
            "Error in reading from %s.\nIt should be in below format seperated by single space:\n\n\tschema 12.0.7.8855\n\n. Error is : %s" % ( UPGRADE_LIST, e))
        sys.exit(2)


if __name__ == "__main__":
    main()
