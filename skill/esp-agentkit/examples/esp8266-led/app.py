# Tested on NodeMCU V3 / ESP-12F with the onboard blue LED on D4/GPIO2.
# The LED is active-low. Confirm the pin and polarity on the actual board
# before running this example. GPIO2 must be high at reset for normal startup.
# Record that check and any wiring changes in the device project.
# ESP8266 pin reference:
# https://docs.micropython.org/en/v1.29.0/esp8266/quickref.html#pins-and-gpio

from machine import Pin
from time import sleep_ms


led = Pin(2, Pin.OUT, value=1)  # D4 sinks current through the blue LED.
print("Onboard LED: triple flash on D4/GPIO2")
try:
    while True:
        for off_ms in (200, 200, 1400):
            led.value(0)
            sleep_ms(200)
            led.value(1)
            sleep_ms(off_ms)
finally:
    led.value(1)
