import pygame
import time

pygame.init()
pygame.joystick.init()

if pygame.joystick.get_count() == 0:
    print("Joystick tidak ditemukan")
    raise SystemExit

js = pygame.joystick.Joystick(0)
js.init()

print("Joystick:", js.get_name())
print("Axes:", js.get_numaxes())
print("Buttons:", js.get_numbuttons())
print()
print("Gerakkan SATU stick/tombol setiap kali.")
print("CTRL+C untuk keluar.")
print()

while True:
    pygame.event.pump()

    axes = [
        round(js.get_axis(i), 3)
        for i in range(js.get_numaxes())
    ]

    buttons = [
        js.get_button(i)
        for i in range(js.get_numbuttons())
    ]

    print(
        "AXIS:",
        axes,
        " BUTTON:",
        buttons,
        end="\r",
        flush=True,
    )

    time.sleep(0.05)