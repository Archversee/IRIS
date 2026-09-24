"""OPTIONAL - runs on the SIM PC. Two jobs:
  1. raw stick/throttle/rudder positions (input timing before the sim's curves)
  2. the joystick PUSH-TO-TALK button - lets the trainee hold a Warthog button
     to talk to MAVIS instead of touching the tablet.

The only thing it needs: pip install pygame

Usage (on sim PC):
  python hid_forwarder.py --to <laptop-ip> --ptt-button 2     # PTT on the stick
  python hid_forwarder.py --find-button                       # press buttons, see device + index

Gotcha this file handles: BOTH Warthog units contain "Warthog" in their name
("Joystick - HOTAS Warthog" and "Throttle - HOTAS Warthog"), so matching must
be careful. Default --ptt-device is "stick", which matches only the joystick.

Sends ~30 Hz UDP JSON: {"t":..., "pitch":..., "roll":..., "yaw":..., "throttle":..., "ptt":0|1}
"""
import argparse
import json
import socket
import time

# per-device axis maps; FIRST key that matches a device name wins (ordered!)
# so "Throttle - HOTAS Warthog" hits "throttle" before it can hit "warthog".
AXES_ORDERED = (
    ("throttle", {"throttle": (2, -1)}),
    ("vpc",      {"yaw": (0, 1)}),         # Virpil pedals report as "VPC ..."
    ("rudder",   {"yaw": (0, 1)}),
    ("warthog",  {"roll": (0, 1), "pitch": (1, 1)}),   # the stick
    ("stick",    {"roll": (0, 1), "pitch": (1, 1)}),
)


def _devices():
    import pygame
    pygame.init()
    pygame.joystick.init()
    sticks = []
    for i in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(i)
        js.init()
        sticks.append(js)
    return pygame, sticks


def find_button():
    """Interactive: press any button on any device to learn its device + index."""
    pygame, sticks = _devices()
    for i, js in enumerate(sticks):
        print(f"device {i}: {js.get_name()}  ({js.get_numbuttons()} buttons)")
    if not sticks:
        print("no joystick devices found")
        return
    print("\nPress your chosen PTT button now, Ctrl+C to quit.")
    last = {}
    while True:
        pygame.event.pump()
        for js in sticks:
            for b in range(js.get_numbuttons()):
                v = js.get_button(b)
                k = (js.get_instance_id(), b)
                if v and not last.get(k):
                    print(f"\n  {js.get_name()}  button {b}  DOWN")
                    print(f"  → use: --ptt-device \"{js.get_name()}\" --ptt-button {b}")
                last[k] = v
        time.sleep(0.02)


def pick_ptt_device(sticks, want: str):
    want = want.lower()
    matches = [js for js in sticks if want in js.get_name().lower()]
    if len(matches) > 1:
        # ambiguous (e.g. "warthog" matches stick AND throttle): prefer non-throttle
        non_thr = [js for js in matches if "throttle" not in js.get_name().lower()]
        if non_thr:
            matches = non_thr
    return matches[0] if matches else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", help="laptop IP running the instructor app")
    ap.add_argument("--port", type=int, default=6947)
    ap.add_argument("--ptt-device", default="stick",
                    help='device-name substring holding the PTT button (default "stick" '
                         '= the Warthog joystick, NOT the throttle)')
    ap.add_argument("--ptt-button", type=int, default=2,
                    help="button index for push-to-talk (find it with --find-button)")
    ap.add_argument("--find-button", action="store_true",
                    help="interactive: press buttons to print device + index, then exit")
    args = ap.parse_args()

    if args.find_button:
        find_button()
        return
    if not args.to:
        ap.error("--to <laptop-ip> is required (or use --find-button)")

    pygame, sticks = _devices()
    print("connected devices:")
    for js in sticks:
        print(f"  - {js.get_name()}  ({js.get_numbuttons()} buttons)")

    mapped = []
    for js in sticks:
        name = js.get_name().lower()
        for key, axes in AXES_ORDERED:
            if key in name:
                mapped.append((js, axes))
                print(f"axes:  {js.get_name()} -> {list(axes)}")
                break                       # one profile per device, no double-mapping

    ptt_js = pick_ptt_device(sticks, args.ptt_device)
    if ptt_js is not None:
        nb = ptt_js.get_numbuttons()
        if args.ptt_button >= nb:
            print(f"WARNING: {ptt_js.get_name()} has only {nb} buttons; "
                  f"button {args.ptt_button} will never fire")
        print(f"PTT:   {ptt_js.get_name()}  button {args.ptt_button}  (hold to talk)")
    else:
        print(f"WARNING: no device matching {args.ptt_device!r}, PTT disabled. "
              f"Run --find-button and pass --ptt-device \"<name>\"")

    if not mapped and ptt_js is None:
        print("nothing to forward, exiting")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (args.to, args.port)
    print(f"forwarding to udp://{args.to}:{args.port}  (Ctrl+C to stop)")
    if ptt_js is not None:
        print("press the PTT button now, you should see 'PTT DOWN' here")
    ptt_last = False
    while True:
        pygame.event.pump()
        out = {"t": time.time()}
        for js, axes in mapped:
            for logical, (idx, sign) in axes.items():
                try:
                    v = js.get_axis(idx) * sign
                except Exception:
                    continue
                out[logical] = round((v + 1) / 2, 4) if logical == "throttle" else round(v, 4)
        if ptt_js is not None:
            try:
                down = bool(ptt_js.get_button(args.ptt_button))
            except Exception:
                down = False
            out["ptt"] = 1 if down else 0
            if down != ptt_last:
                print("PTT", "DOWN" if down else "UP")
                ptt_last = down
        sock.sendto(json.dumps(out).encode(), dest)
        time.sleep(1 / 30)


if __name__ == "__main__":
    main()
