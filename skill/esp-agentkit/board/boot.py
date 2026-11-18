# ESP Agent Kit Wi-Fi and WebREPL bootstrap.
import errno
import esp
import json
import network
import os
import time

SAVED_CONFIG = "/agentkit_config.json"
PENDING_CONFIG = "/.agentkit-wifi-pending.json"


def start_management():
    # Vendor Wi-Fi diagnostics can include the network name on the serial console.
    if hasattr(esp, "LOG_NONE"):
        esp.osdebug(0, esp.LOG_NONE)
    else:
        esp.osdebug(None)
    try:
        try:
            config_file = open(PENDING_CONFIG)
            fresh_credentials = True
        except OSError as exc:
            if exc.args[0] != errno.ENOENT:
                raise
            config_file = open(SAVED_CONFIG)
            fresh_credentials = False
        with config_file:
            config = json.load(config_file)
        ssid = config["ssid"]
        wifi_password = config["wifi_password"]
        password = config["webrepl_password"]
        if not isinstance(ssid, str) or not ssid or not isinstance(wifi_password, str):
            raise ValueError
        if not isinstance(password, str) or not 4 <= len(password) <= 9:
            raise ValueError
        if password == wifi_password or any(not 32 <= ord(c) <= 126 for c in password):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        print("ESP_AGENTKIT_ERROR config_invalid")
        return

    try:
        station = network.WLAN(network.WLAN.IF_STA)
        station.active(True)
        # The WebREPL listener binds to every active interface.
        network.WLAN(network.WLAN.IF_AP).active(False)
        # The ESP8266 SDK can join a saved network before boot.py runs.
        started = time.ticks_ms()
        connected = station.isconnected()
        if fresh_credentials or not connected or station.config("ssid") != ssid:
            active_link_states = (network.STAT_CONNECTING, network.STAT_GOT_IP)
            if connected or (fresh_credentials and station.status() in active_link_states):
                station.disconnect()
                # Disconnect is asynchronous. A stale lease cannot confirm the new key.
                while station.status() in active_link_states:
                    if time.ticks_diff(time.ticks_ms(), started) >= 20000:
                        raise RuntimeError
                    time.sleep_ms(100)
            station.connect(ssid, wifi_password)
        while not station.isconnected() and time.ticks_diff(time.ticks_ms(), started) < 20000:
            time.sleep_ms(100)
        if station.isconnected():
            if fresh_credentials:
                try:
                    os.rename(PENDING_CONFIG, SAVED_CONFIG)
                except OSError:
                    print("ESP_AGENTKIT_ERROR config_commit_failed")
                    return
            status = "ESP_AGENTKIT_READY", station.ifconfig()[0]
        elif fresh_credentials and station.status() == network.STAT_WRONG_PASSWORD:
            status = "ESP_AGENTKIT_ERROR", "wifi_auth_failed"
        else:
            mac = ":".join("%02x" % b for b in station.config("mac"))
            status = "ESP_AGENTKIT_PENDING", mac
    except (OSError, ValueError, RuntimeError):
        print("ESP_AGENTKIT_ERROR wifi_failed")
        return

    try:
        import webrepl

        # The wildcard listener can also accept connections after a late DHCP lease.
        webrepl.start(password=password)
    except (ImportError, OSError, ValueError, RuntimeError):
        print("ESP_AGENTKIT_ERROR webrepl_failed")
        return
    print(*status)


start_management()
