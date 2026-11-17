# ESP Agent Kit USB provisioning bootstrap for the ESP32-DevKitC V4.
import esp
import json
import network
import time


def start_management():
    # Vendor Wi-Fi diagnostics can include the network name on the serial console.
    esp.osdebug(0, esp.LOG_NONE)
    try:
        with open("/agentkit_config.json") as config_file:
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
        # The WebREPL listener binds to every active interface.
        network.WLAN(network.WLAN.IF_AP).active(False)
        station = network.WLAN(network.WLAN.IF_STA)
        station.active(True)
        if not station.isconnected():
            station.connect(ssid, wifi_password)
        started = time.ticks_ms()
        while not station.isconnected() and time.ticks_diff(time.ticks_ms(), started) < 20000:
            time.sleep_ms(100)
        if station.isconnected():
            status = "ESP_AGENTKIT_READY", station.ifconfig()[0]
        else:
            # The ESP32 station keeps reconnecting after boot.py returns.
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
