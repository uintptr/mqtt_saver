#!/usr/bin/env python3

import sys
import argparse
import subprocess

import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage

DEF_MQTT_SERVER = "10.0.0.2"
DEF_MQTT_PORT = 1883


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
    exec(cmd_line)


def on_message(client: mqtt.Client, userdata: str, msg: MQTTMessage) -> None:

    payload = msg.payload.decode("utf-8")

    if "away" == payload:
        start_screen_saver()
    else:
        print(f"Unknown payload \"{payload}\"")


def main() -> int:

    status = 1

    parser = argparse.ArgumentParser()

    parser.add_argument("-s",
                        "--server",
                        type=str,
                        default=DEF_MQTT_SERVER,
                        help=f"MQTT server. Default: {DEF_MQTT_SERVER}")

    parser.add_argument("-p",
                        "--port",
                        type=int,
                        default=DEF_MQTT_PORT,
                        help=f"MQTT server port. Default: {DEF_MQTT_PORT}")

    try:
        args = parser.parse_args()

        client = mqtt.Client()
        client.connect(args.server, args.port)
        client.on_message = on_message
        client.subscribe("/motion/office")
        client.loop_forever()

        status = 0
    except KeyboardInterrupt:
        pass

    return status


if __name__ == '__main__':

    status = main()

    if 0 != status:
        sys.exit(status)
