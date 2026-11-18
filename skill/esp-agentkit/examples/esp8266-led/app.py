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
