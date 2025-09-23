#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import logging
import shutil

from abc import ABC, abstractmethod

from typing import Any, override, final
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage, ConnectFlags, DisconnectFlags
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.properties import Properties

from jsonconfig import JSONConfig

DEF_MQTT_SERVER = "localhost"


class ShellExecError(Exception):

    def __init__(self, cmd_line: str, ret: int, stdout: str, stderr: str) -> None:

        super().__init__()

        err_msg = f"{cmd_line} returned {ret}"

        if "" != stdout:
            err_msg += f"\n{stdout}"

        if "" != stderr:
            err_msg += f"\n{stderr}"

        self.err_msg: str = err_msg

    @override
    def __str__(self) -> str:
        return self.err_msg


@dataclass
class MQTTTopic:
    topic: str
    payload: str
    command: str | None = None
    osd: str | None = None


def exec_text_command(cmd_line: str, check: bool = True) -> tuple[int, str, str]:

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
            raise ShellExecError(cmd_line, ret, stdout, stderr)

        return ret, stdout, stderr


class MqttNotify(ABC):

    @abstractmethod
    def display_text(self, text: str) -> None:
        pass


@final
class OSD(MqttNotify):
    def __init__(self, text_size: int = 90, text_color: str = "white") -> None:
        self.text_size = text_size
        self.text_color = text_color

    def __get_geometry_dpy(self) -> tuple[int, int]:

        _, stdout, _ = exec_text_command("xdpyinfo")

        for line in stdout.splitlines():

            if "dimensions:" not in line:
                continue

            geo_str = line.split()[1]

            w_str, h_str = geo_str.split("x")

            return int(w_str), int(h_str)

        raise NotImplementedError("couldn't find desktop's geometry")

    def __get_geometry(self) -> tuple[int, int]:

        _, stdout, _ = exec_text_command("xrandr")

        for line in stdout.splitlines():
            if " primary " not in line:
                continue
            geo_str = line.split()[3].split("+")[0]

            w_str, h_str = geo_str.split("x")

            return int(w_str), int(h_str)

        return self.__get_geometry_dpy()

    @override
    def display_text(self, text: str) -> None:

        width, height = self.__get_geometry()

        text_width = len(text) * self.text_size

        y = int((height / 2) - (self.text_size / 4))
        x = int((width / 2) - (text_width / 4))

        cmd_line = f"echo \"{text}\" | aosd_cat -x {x} -y -{y} -w {text_width}"
        cmd_line += f" -R {self.text_color}"
        cmd_line += f" -n {self.text_size}"

        _ = exec_text_command(cmd_line)


class DunstNotify(MqttNotify):
    def __init__(self) -> None:
        pass

    @override
    def display_text(self, text: str) -> None:

        cmd_line = f"dunstify {text}"
        exec_text_command(cmd_line)


@final
class MQTTCallbacks:

    def __init__(self, config: JSONConfig, verbose: bool, dry_run: bool) -> None:
        self.verbose: bool = verbose
        self.dry_run: bool = dry_run

        notify_type = config.get_str("/notify", default="osd")

        if notify_type == "osd":
            self.notify = OSD()
        elif notify_type == "dunst":
            self.notify = DunstNotify()
        else:
            raise NotImplementedError()

        self.sub_topic_list: list[tuple[str, int]] = []
        self.topics: dict[str, MQTTTopic] = {}

        for t in config.get_list("/topics", []):
            entry = MQTTTopic(**t)

            # make it easy to search
            self.topics[entry.topic] = entry
            self.sub_topic_list.append((entry.topic, 0))

    def __parse_topic_command(self, topic: MQTTTopic) -> None:

        if topic.command is None:
            return

        logging.info(f"executing \"{topic.command}\"")

        if True == self.dry_run:
            return

        ret, stdout, stderr = exec_text_command(topic.command, check=False)

        if 0 != ret:
            err_msg = f"{topic.command} returned {ret}"

            if "" != stdout:
                err_msg += f"\n{stdout}"

            if "" != stderr:
                err_msg += f"\n{stderr}"
            logging.error(err_msg)

    def __parse_topic_osd(self, topic: MQTTTopic) -> None:

        if topic.osd is None:
            return

        logging.info(f"displaying \"{topic.osd}\"")

        self.notify.display_text(topic.osd)

    def __parse_topic(self, topic: MQTTTopic) -> None:

        try:
            if topic.command is not None:
                self.__parse_topic_command(topic)

            if topic.osd is not None:
                self.__parse_topic_osd(topic)
        except ShellExecError as e:
            logging.error(str(e))

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
            self.__parse_topic(entry)
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


def check_requirements() -> None:

    avail = True

    shell_commands = ["xrandr", "aosd_cat", "xdpyinfo"]

    for c in shell_commands:

        path = shutil.which(c)

        if path is None:
            print(f"ERROR: \"{c}\" command was not found in path")
            avail = False

    assert True == avail, "Missing requirements"

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

        check_requirements()

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
    except AssertionError:
        pass

    return status


if __name__ == '__main__':

    status = main()

    if 0 != status:
        sys.exit(status)
