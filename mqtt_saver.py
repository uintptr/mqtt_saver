#!/usr/bin/env python3

import os
import sys
import argparse
import subprocess
import logging

from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage, ConnectFlags, DisconnectFlags
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.properties import Properties

DEF_MQTT_SERVER = "10.0.0.2"


class ShellExecError(Exception):
    pass


def exec(cmd_line: str, cwd: str | None = None, check: bool = True) -> tuple[int, str, str]:

    ret = 1
    with subprocess.Popen(cmd_line,
                          shell=True,
                          text=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as p:

        stdout, stderr = p.communicate()

        if p.returncode is not None:
            ret = p.returncode

        if True == check and 0 != ret:
            raise ShellExecError(f"{cmd_line} returned {ret}")

        return ret, stdout, stderr


def start_screen_saver() -> None:

    cmd_line = "xset dpms force off"
    ret, stdout, stderr = exec(cmd_line, check=False)

    logging.info(f"{cmd_line} -> {ret}")

    if 0 != ret:
        err_msg = f"{cmd_line} returned {ret}"
        err_msg += f"stdout={stdout} stderr={stderr}"
        logging.error(err_msg)


class MQTTCallbacks:

    def __init__(self, verbose: bool, dry_run: bool) -> None:
        self.verbose = verbose
        self.dry_run = dry_run

    def on_connect(self, client: mqtt.Client, userdata: Any, connect_flags: ConnectFlags, reason_code: ReasonCode, properties: Properties | None) -> None:
        logging.info(f"connected. reason={reason_code}")
        if 0 == reason_code.value:
            client.subscribe("/motion/office")

    def on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: DisconnectFlags, reason_code: ReasonCode, props: Properties | None) -> None:
        logging.info(f"disconnected. reason={reason_code}")

    def on_log(self, client: mqtt.Client, userdata: Any, reason_code: int, log: str) -> None:

        if True == self.verbose:
            logging.info(log)

    def on_message(self, client: mqtt.Client, userdata: str, msg: MQTTMessage) -> None:

        payload = msg.payload.decode("utf-8")

        logging.info(f"{msg.topic} -> {payload}")

        if True == self.dry_run:
            return

        if "away" == payload:
            start_screen_saver()
        else:
            print(f"Unknown payload \"{payload}\"")


def init_logging(verbose: bool, file_name: str = "logs.log"):

    script_root = os.path.abspath(os.path.dirname(sys.argv[0]))
    log_file = os.path.join(script_root, file_name)

    formatter = logging.Formatter(
        '%(created)-18s - %(levelname)s - %(message)s')

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)


def main() -> int:

    status = 1

    parser = argparse.ArgumentParser()

    parser.add_argument("-s",
                        "--server",
                        type=str,
                        default=DEF_MQTT_SERVER,
                        help=f"MQTT server. Default: {DEF_MQTT_SERVER}")

    parser.add_argument("-v",
                        "--verbose",
                        action="store_true",
                        help="verbose")

    parser.add_argument("-d",
                        "--dry-run",
                        action="store_true",
                        help="Don't execute anything, just log")
    try:
        args = parser.parse_args()

        init_logging(args.verbose)

        logging.info("=" * 80)

        cb = MQTTCallbacks(args.verbose, args.dry_run)

        client = mqtt.Client(CallbackAPIVersion.VERSION2)
        client.connect(args.server, keepalive=10)
        client.on_message = cb.on_message
        client.on_connect = cb.on_connect
        client.on_disconnect = cb.on_disconnect
        client.on_log = cb.on_log

        client.loop_forever()

        status = 0

    except KeyboardInterrupt:
        pass

    return status


if __name__ == '__main__':

    status = main()

    if 0 != status:
        sys.exit(status)
