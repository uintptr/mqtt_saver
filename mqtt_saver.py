#!/usr/bin/env python3

import os
import sys
import argparse
import subprocess
import logging

from typing import Any
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage, ConnectFlags, DisconnectFlags
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.properties import Properties

from jsonconfig import JSONConfig

DEF_MQTT_SERVER = "localhost"


class ShellExecError(Exception):
    pass


@dataclass
class MQTTTopic:
    topic: str
    payload: str
    command: str


class MQTTCallbacks:

    def __init__(self, config: JSONConfig, verbose: bool, dry_run: bool) -> None:
        self.verbose = verbose
        self.dry_run = dry_run

        self.sub_topic_list: list[tuple[str, int]] = []
        self.topics: dict[str, MQTTTopic] = {}

        for t in config.get_list("/topics", []):
            entry = MQTTTopic(**t)

            # make it easy to search
            self.topics[entry.topic] = entry
            self.sub_topic_list.append((entry.topic, 0))

    def __exec(self, cmd_line: str, cwd: str | None = None, check: bool = True) -> tuple[int, str, str]:

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

    def __execute_topic(self, topic: MQTTTopic) -> None:

        logging.info(f"executing \"{topic.command}\"")

        if True == self.dry_run:
            return

        ret, stdout, stderr = self.__exec(topic.command, check=False)

        if 0 != ret:
            err_msg = f"{topic.command} returned {ret}"

            if "" != stdout:
                err_msg += f"\n{stdout}"

            if "" != stderr:
                err_msg += f"\n{stderr}"
            logging.error(err_msg)

    def on_connect(self, client: mqtt.Client, userdata: Any, connect_flags: ConnectFlags, reason_code: ReasonCode, properties: Properties | None) -> None:
        logging.info(f"connected. reason={reason_code}")

        if 0 == reason_code.value and len(self.sub_topic_list) > 0:
            client.subscribe(self.sub_topic_list)

    def on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: DisconnectFlags, reason_code: ReasonCode, props: Properties | None) -> None:
        logging.info(f"disconnected. reason={reason_code}")

    def on_log(self, client: mqtt.Client, userdata: Any, reason_code: int, log: str) -> None:

        if True == self.verbose:
            logging.info(log)

    def on_message(self, client: mqtt.Client, userdata: str, msg: MQTTMessage) -> None:

        payload = msg.payload.decode("utf-8")

        logging.debug(f"{msg.topic} -> {payload}")

        if msg.topic not in self.topics:
            logging.warning(f"ignoring {msg.topic}")
            return

        entry = self.topics[msg.topic]

        if payload == entry.payload:
            self.__execute_topic(entry)
        else:
            logging.warning(f"payload not handled. payload=\"{payload}\"")


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

    script_root = os.path.abspath(os.path.dirname(sys.argv[0]))
    def_config_file = os.path.join(script_root, "config.json")

    parser.add_argument("-c",
                        "--config",
                        type=str,
                        default=def_config_file,
                        help=f"/path/to/config.json. Default: {def_config_file}")

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

        config = JSONConfig(args.config)

        host = config.get_str("/server/host")  # mandatory
        keepalive = config.get_int("/server/keep_alive", 10)
        port = config.get_int("/server/port", 1883)

        init_logging(args.verbose)

        logging.info("=" * 80)

        cb = MQTTCallbacks(config, args.verbose, args.dry_run)

        client = mqtt.Client(CallbackAPIVersion.VERSION2)
        client.connect(host,
                       port=port,
                       keepalive=keepalive)
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
