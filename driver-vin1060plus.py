#############################################################################################################
# Driver for tablette VINSA 1060 plus based on «f-caro» (https://github.com/f-caro), and inspired by
# Alexandr Vasilyev - «alex-s-v» (https://github.com/alex-s-v)
#
# I've put everything inside this script (reclaim interface) - open full area - source of Alexandr and
# I 've added the management of buttons on top of the tablet. 
# 21/09/2024 - Delfosse Aurore (ON7AUR) - V0.1 - first release.
# 30/06/2025 - Debugging ond fix for pen buttons support and some optimalizations (key map) by szdowk.
# 18/10/2025 - Debugging of "Resource busy" error, sleep/hibernate support and usb errors handle by szdowk.
# 15/01/2026 - Still more USB communication hardening by szdowk.
# 15/06/2026 - I encountered strange behavior of the tablet after some system upgrades inc. kernel 5.15.209,
#              xorg 1.20.14 and wayland 21.1.4. I'm not sure what was a exact cause. Anyway it looks like
#              a new buttons logic and handling of timeouts solved this problem. Check config file for
#              changes. This version was tested for usage as mouse and with Gimp tools depended on pressure.
#              Test platform is a patched Slackware 15 x86_64 with "testing" version of binutils-gcc-glibc.
#              (!)15.06.2026 comment: Anyway, currently mixed-mode devices (configured as mouse and tablet at
#                once) do not work properly.  Previously it was possible to configure "pen_touch: -BTN_MOUSE"
#                (or -BTN_LEFT) and "pen: BTN_TOOL_PEN" at once what gave effect of perfect mouse
#                emulation and detection of stylus tip pressure in Gimp and Krita.
#                Unfortunately, currently, declaration "pen: BTN_TOOL_PEN" have strange behavior. It adds
#                additional mouse button click&hold (BTN_LEFT=1 without contact between stylus tip and tablet
#                surface) on xorg level when a stylus go into tablet radius. You will feel it as very
#                annoying behavior.
#                So, if you will work with this device just as mouse or pointer, comment out
#                "pen: BTN_TOOL_PEN" declaration in config file. Gimp and Krita will work, however without
#                stylus tip pressure detection. (szdowk)
# 16/06/2026 - OK. It appeared, that with "pen: BTN_TOOL_PEN" option, libinput/X generate its own "button
#              press 1/0" (BTN_LEFT=1/0) based only on tip pressure(!). This tablet never return pressure=0
#              (even when the stylus tip hoover in the air above tablet surface the pressure is equal to
#              175...180), so "X" always generated its own BTN_LEFT=1 when the stylus go into the radius
#              of tablet and BTN_LEFT=0 when go out. Fixed now. The tablet should work as mouse and pressure
#              tool at once with "pen: BTN_TOOL_PEN" declaration in config file. (szdowk)
# 17/06/2026 - Top buttons work again(?). At least "mute","volume up" and "volume down". The rest top buttons
#              should be configured in KDE.
#############################################################################################################

import os
import sys
import shutil
import time
# Specification of the device https://python-evdev.readthedocs.io/en/latest/
from evdev import UInput, ecodes, AbsInfo
# Establish usb communication with device
import usb
import usb.core
import usb.util
# Read configuration
import yaml

# ##########################################################################################################
# Global variables
# ##########################################################################################################
DEBUG = False        # Default mode, try "--debug" line option for debug mode.
RESET_COOLDOWN_S = 10.0     # do not do full setup more frequent than x s
SHORT_STREAK_SOFT = 5       # after those amount of short frames: soft re-claim iface 1
SHORT_STREAK_HARD = 30      # after those amount of short frames: full setup (probe/full area)

# ##########################################################################################################
# Options
# ##########################################################################################################
for arg in sys.argv[1:]:
    if arg == "--debug": DEBUG = True

# ##########################################################################################################
# Functions
# ##########################################################################################################
def probe(bus_num, dev_addr):
    # Initialisation du contexte USB
    dev = usb.core.find(bus=bus_num, address=dev_addr)
    
    if dev is None:
        print("Device not found")
        return 1

    # Exemple de transmission de rapports
    def set_report(wValue, report):
        try:
            dev.ctrl_transfer(0x21, 9, wValue, 2, report, 250)
        except usb.core.USBError as e:
            print(f"Error setting report: {e}")
            return 1
        return 0

    reports = [
        (0x0308, [0x08, 0x04, 0x1d, 0x01, 0xff, 0xff, 0x06, 0x2e]),
        (0x0308, [0x08, 0x03, 0x00, 0xff, 0xf0, 0x00, 0xff, 0xf0]),
        (0x0308, [0x08, 0x06, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00]),
        (0x0308, [0x08, 0x03, 0x00, 0xff, 0xf0, 0x00, 0xff, 0xf0]),
    ]
    
    for i, (wValue, report) in enumerate(reports):
        result = set_report(wValue, report)
        if result != 0:
            print(f"Failed at report {i}")
            return result

    # Libération de l'interface
    usb.util.release_interface(dev, 2)
    return 0

def run_probe_on_iface2(dev):
    """Zakłada, że iface 2 jest już zclaimowany; wysyła sekwencję raportów 'full area'."""
    def set_report(wValue, report):
        dev.ctrl_transfer(0x21, 9, wValue, 2, report, 250)
        time.sleep(0.01)
    set_report(0x0308, [0x08, 0x04, 0x1d, 0x01, 0xff, 0xff, 0x06, 0x2e])
    set_report(0x0308, [0x08, 0x03, 0x00, 0xff, 0xf0, 0x00, 0xff, 0xf0])
    set_report(0x0308, [0x08, 0x06, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
    set_report(0x0308, [0x08, 0x03, 0x00, 0xff, 0xf0, 0x00, 0xff, 0xf0])

def setup_device_for_full_area(dev):
    # 0) Try to unload kernel drivers (nothing will happen if already unloaded)
    for i in (0, 1, 2):
        try:
            if dev.is_kernel_driver_active(i):
                dev.detach_kernel_driver(i)
        except Exception:
            pass

    # Reset
    try:
        dev.reset()
    except usb.core.USBError:
        # after resume some controlers do reset – lets ignore
        pass

    # set_configuration with  retry i EBUSY (16) support
    for _ in range(3):
        try:
            dev.set_configuration()
            break
        except usb.core.USBError as e:
            err = getattr(e, "errno", None) or (e.args[0] if e.args else None)
            if err == 16:  # Resource busy
                # Lets try again and additional detach
                for i in (0, 1, 2):
                    try:
                        if dev.is_kernel_driver_active(i):
                            dev.detach_kernel_driver(i)
                    except Exception:
                        pass
                time.sleep(0.05)
                continue
            else:
                raise

    # full-area: claim iface 2, reports, release 2
    usb.util.claim_interface(dev, 2)
    run_probe_on_iface2(dev)
    usb.util.release_interface(dev, 2)

    # read from iface 1
    usb.util.claim_interface(dev, 1)
    ep = dev[0].interfaces()[1].endpoints()[0]
    return ep


# ##########################################################################################################
# Main #####################################################################################################
# ##########################################################################################################
if __name__ == "__main__":
    # Check existing directory ~/.config/driver-vin1060plus - if not create it and Copy inside the yaml
    #if not os.path.exists(os.path.join(os.path.expanduser(".config/driver-vin1060plus"), "config-vin1060plus.yaml" )):
    if not os.path.exists(os.path.join(os.path.expanduser('~'), ".config/config-vin1060plus/config-vin1060plus.yaml" )):
        print("yaml not found")
        # creat directory
        print(f"create directory : {os.path.join(os.path.expanduser('~'), '.config/config-vin1060plus')}")
        os.makedirs(os.path.join(os.path.expanduser('~'), ".config/config-vin1060plus"))
        # copy file 
        shutil.copy("./config-vin1060plus.yaml", os.path.join(os.path.expanduser('~'), ".config/config-vin1060plus/config-vin1060plus.yaml" ))

    # Open yaml file from :
    path = os.path.join(os.path.expanduser('~'), ".config/config-vin1060plus/config-vin1060plus.yaml")
    # if we compile the solution with Nuitka - user should be able to map his own keys via this file

    # Loading tablet configuration
    with open(path, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    # tabus=$(lsusb|grep 08f2:6811|gawk '{print $2;print $4}'|tr '\n' ' '|tr ':' ' ')
    # -> adress is in the yaml
    tabus=usb.core.find(idVendor=config["vendor_id"], idProduct=config["product_id"])
    if tabus==None :
        print("Vinsa 1060Plus not found - check if vendor:product correspond from «lsusb» and in the config File")
        print(f"Your config file is :{os.path.join(os.path.expanduser('~'), '.config/config-vin1060plus/config-vin1060plus.yaml')}")
        exit(-1)

    probe(tabus.bus, tabus.address)
    # HERE : Full area open
    # ######################################################################################################

    # Get the required ecodes from configuration
    pen_codes = []
    btn_codes = []
    for k, v in config["actions"].items():
        codes = btn_codes if k == "tablet_buttons" else pen_codes
        if isinstance(v, list):
            codes.extend(v)
        else:
            codes.append(v)

    if(DEBUG) : print("codes", codes)
    if(DEBUG) : print("pen_codes", pen_codes)
    if(DEBUG) : print("btn_codes", btn_codes)

    temp = []
    for c in pen_codes:
        temp.extend([ecodes.ecodes[x] for x in c.split("+")])
    pen_codes = temp

    temp = []
    for c in btn_codes:
        temp.extend([ecodes.ecodes[x] for x in c.split("+")])
    btn_codes = temp

    pen_events = {
        ecodes.EV_KEY: pen_codes,
        ecodes.EV_ABS: [
            #AbsInfo input: value, min, max, fuzz, flat
            (ecodes.ABS_X, AbsInfo(0, 0, config["pen"]["max_x"], 0, 0, config["pen"]["resolution_x"])),         
            (ecodes.ABS_Y, AbsInfo(0, 0, config["pen"]["max_y"], 0, 0, config["pen"]["resolution_y"])),
            #dont calculate absolute x-max/x-min or y-max/y-min values when multiple displays used
            #rather use xrandr and xinput together to configure which display handles the virtual pen ID
            #eg. xinput map-to-output 17 DisplayPort-1
            (ecodes.ABS_PRESSURE, AbsInfo(0, 0, config["pen"]["max_pressure"], 0, 0, 1))
        ],
    }

    btn_events = {ecodes.EV_KEY: btn_codes}
    if(DEBUG) : print("pen_events :: ", pen_events)
    if(DEBUG) : print("btn_events :: ", btn_events)

    # Find the device
    dev = usb.core.find(idVendor=config["vendor_id"], idProduct=config["product_id"])
    # Select end point for reading second interface [2] for actual data
    # Interface[0] associated Internal USB storage (labelled as CDROM drive)
    # Interface[1] useful to map 'Full Tablet Active Area' -- outputs 64 bytes of xinput events
    # Interface[2] maps to the 'AndroidActive Area' --- outputs 8 bytes of xinput events ( but only before  ./10moons-probe is executed)
    if(DEBUG) : print(dev)
    if(DEBUG) : print("--------------------------------")
    if(DEBUG) : print("---Drop default kernel driver from all devices")

    if(DEBUG): print("--- setup_device_for_full_area(dev)")
    ep = setup_device_for_full_area(dev)

    vpen = UInput(events=pen_events, name=config["xinput_name"], version=0x3)
    if(DEBUG) : print(vpen.capabilities(verbose=True).keys() )
    if(DEBUG) : print(vpen.capabilities(verbose=True) )
    vbtn = UInput(events=btn_events, name=config["xinput_name"] + "_buttons", version=0x3)  

    #compare with xinput list
    if(DEBUG) : print(vbtn.capabilities(verbose=True).keys() )
    if(DEBUG) : print(vbtn.capabilities(verbose=True) )
    if(DEBUG) : print(vpen)
    if(DEBUG) : print(vbtn)

    pressed = -1

    # Direction and axis configuration
    max_x = config["pen"]["max_x"] * config["settings"]["swap_direction_x"]
    max_y = config["pen"]["max_y"] * config["settings"]["swap_direction_y"]
    if config["settings"]["swap_axis"]:
        y1, y2, x1, x2 = (1, 2, 3, 4) 
    else:
        x1, x2, y1, y2 = (1, 2, 3, 4)
        
    #Pen pressure thresholds:
    pressure_max = config["pen"]["max_pressure"]
    pressure_min = config["pen"]["pressure_min"]
    pressure_contact_threshold = config["pen"]["pressure_contact_threshold"]
    pressure_contact_threshold_up = config["pen"]["pressure_contact_threshold_up"]
    #Unfortunately vin1060plus does not show 8192 pressure resolution.  #TODO: need to review pressure parameters

    pressed_prev = None
    pen_pressed_prev = None

    #non symetric pressure threshold while changing button state down/up
    pen_contact_prev = False
    pressure_release_threshold = max(0, pressure_contact_threshold - pressure_contact_threshold_up)
    
    pen_button_down = False

    button_map = {
        (255, 49):  0,   # key E
        (255, 35):  1,   # key B
        (127, 51):  2,   # CTRL-
        (255, 50):  3,   # CTRL+
        (191, 51):  4,   # [
        (255, 19):  5,   # ]
        (223, 51):  6,   # scroll up
        (254, 51):  7,   # TAB
        (239, 51):  8,   # scroll down
        (253, 51):  9,   # SPACE
        (247, 51): 10,   # CTRL
        (251, 51): 11,   # ALT
    }

    # ######################################################################################################
    # Infinite loop
    # ######################################################################################################

    short_streak = 0
    idle_timeouts = 0
    last_hard_reset = 0.0
    short_streak = 0
    last_hard_reset = 0.0
    top_button_prev = None

    while True:
        try:
            # 1) Read with timeout (1 s)
            try:
                data = dev.read(ep.bEndpointAddress, ep.wMaxPacketSize, timeout=1000)
                idle_timeouts = 0  # if recaived data
            except usb.core.USBTimeoutError:
                # In some situations except USBTimeoutError there is USBError 110;
                # it will be cached below, so there just re-raise:
                raise
            except usb.core.USBError as e:
                # If time passed, the tablet is inactive
                err = getattr(e, "errno", None)
                if err is None and e.args:
                    err = e.args[0]
                if err == 110:  # Operation timed out
                    idle_timeouts += 1
                    # Idle/timeout means: do not leave any pen button/tool state latched.
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_TOOL_PEN, 0)
                    vpen.syn()
                    pen_contact_prev = False
                    pen_button_down = False
    
                    delay = min(0.05, 0.001 * idle_timeouts)  # 50ms max
                    if DEBUG and idle_timeouts % 60 == 0:
                        print(f"[USB] idle timeout x{idle_timeouts} (delay={delay:.3f}s)")
                    time.sleep(delay)
                    # Lets „refresh” interface after long silence:
                    if idle_timeouts >= 600:  # ~10 minutes of silence
                        if DEBUG: print("[USB] long idle – soft re-claim iface 1")
                        try: usb.util.release_interface(dev, 1)
                        except Exception: pass
                        try: usb.util.claim_interface(dev, 1)
                        except usb.core.USBError: pass
                        ep = dev[0].interfaces()[1].endpoints()[0]
                        idle_timeouts = 0
                    continue
                else:
                    # Other errors, below
                    raise
    
            # ######################################################################
            # THERE IS NEW DATA
            # ######################################################################
            n = len(data)
            if(DEBUG) : print(f"pkt len={n} -> {list(data)}")
            if(DEBUG) : print(data) # shows button pressed array
            pressed = None
            pen_contact = False

            if n < 7:
                short_streak += 1

                # 1) first soft re-claim iface 1 (without device reset)
                if short_streak == SHORT_STREAK_SOFT:
                    if DEBUG: print("[USB] short frames -> soft re-claim iface 1")
                    try:
                        usb.util.release_interface(dev, 1)
                    except Exception:
                        pass
                    time.sleep(0.05)
                    try:
                        usb.util.claim_interface(dev, 1)
                        ep = dev[0].interfaces()[1].endpoints()[0]
                    except usb.core.USBError as e:
                        if DEBUG: print("[USB] soft re-claim failed:", repr(e))

                # 2) if short frames continue -> full setup, with cooldown
                if short_streak >= SHORT_STREAK_HARD:
                    now = time.monotonic()
                    if now - last_hard_reset >= RESET_COOLDOWN_S:
                        if DEBUG: print("[USB] persistent short frames -> full reinit (full area)")
                        try:
                            ep = setup_device_for_full_area(dev)
                            last_hard_reset = now
                            short_streak = 0
                        except usb.core.USBError as e:
                            if DEBUG: print("[USB] full reinit failed:", repr(e))
                            # but do not loop too agressive
                            last_hard_reset = now
                    # even if cooldown blocked, we still do not parse short frame
                continue
            else:
                short_streak = 0

            # press types: 0 - up; 1 - down; 2 - hold
            press_type = 0

            # ##############################################################################################
            # Position & pressure_contact
            if n >= 7 and data[5] in [3,4,5,6]:  # Pen actions
                pen_x = abs(max_x - (data[x1] * 255 + data[x2]))
                pen_y = abs(max_y - (data[y1] * 255 + data[y2]))

                raw_pressure = data[5] * 256 + data[6]
                pen_pressure = pressure_max - raw_pressure

                if pen_pressure < 0:
                    pen_pressure = 0
                elif pen_pressure > pressure_max:
                    pen_pressure = pressure_max

                #pen_contact = pen_pressure >= pressure_contact_threshold
                if pen_contact_prev:
                    pen_contact = pen_pressure >= pressure_release_threshold
                else:
                    pen_contact = pen_pressure >= pressure_contact_threshold

                if DEBUG:
                    print(
                        "pen_x, pen_y:", pen_x, "-", pen_y,
                        " threshold:", pressure_contact_threshold,
                        " contact:", pen_contact,
                        " release_threshold:", pressure_release_threshold,
                        " prev_contact:", pen_contact_prev,
                        " raw_pressure:", raw_pressure,
                        " --- pen_pressure:", pen_pressure
                    )

                # Tablet top button area: nie traktuj jako normalnego kliknięcia pióra.
                if pen_y > 61200 : #and pen_contact:
                    #press_type = 1
                    top_pressed = None
                    if pen_x == 200:
                        if DEBUG: print("mute")
                        top_pressed = 12
                    elif pen_x == 607:
                        if DEBUG: print("vol-")
                        top_pressed = 13
                    elif pen_x == 1015:
                        if DEBUG: print("vol+")
                        top_pressed = 14
                    elif pen_x == 1422:
                        if DEBUG: print("note")
                        top_pressed = 15
                    elif pen_x == 1829:
                        if DEBUG: print("play/pause")
                        top_pressed = 16
                    elif pen_x == 2237:
                        if DEBUG: print("prev")
                        top_pressed = 17
                    elif pen_x == 2644:
                        if DEBUG: print("next")
                        top_pressed = 18
                    elif pen_x == 3052:
                        if DEBUG: print("home")
                        top_pressed = 19
                    elif pen_x == 3459:
                        if DEBUG: print("calc")
                        top_pressed = 20
                    elif pen_x == 3866:
                        if DEBUG: print("Desk")
                        top_pressed = 21

                    if DEBUG: print("We are in pen_y > 61200 block, top_pressed: ", top_pressed)


                    if pen_contact and top_pressed is not None and top_pressed != top_button_prev:
                        key_codes = config["actions"]["tablet_buttons"][top_pressed].split("+")

                        for key in key_codes:
                            act = ecodes.ecodes[key]
                            vbtn.write(ecodes.EV_KEY, act, 1)
                        vbtn.syn()

                        for key in key_codes:
                            act = ecodes.ecodes[key]
                            vbtn.write(ecodes.EV_KEY, act, 0)
                        vbtn.syn()

                        top_button_prev = top_pressed

                    if not pen_contact:
                        top_button_prev = None

                    # Ważne: w strefie górnych przycisków nie zostawiaj kliknięcia pióra.
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_TOOL_PEN, 0)
                    vpen.syn()

                else:
                    top_button_prev = None

                    # Jedna spójna ramka stanu pióra.
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1 if pen_contact else 0)
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_TOOL_PEN, 1)

                    vpen.write(ecodes.EV_ABS, ecodes.ABS_X, pen_x)
                    vpen.write(ecodes.EV_ABS, ecodes.ABS_Y, pen_y)
                    vpen.write(ecodes.EV_ABS, ecodes.ABS_PRESSURE, pen_pressure if pen_contact else 0)

                    vpen.syn()

                    pen_contact_prev = pen_contact
                    pen_button_down = pen_contact

            else:
            # Pakiet poprawnej długości, ale nie jest rozpoznanym pakietem pióra.
            # Nie zostawiaj aktywnego kontaktu.
                if pen_button_down or pen_contact_prev:
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
                    vpen.write(ecodes.EV_KEY, ecodes.BTN_TOOL_PEN, 0)
                    vpen.syn()
                    pen_contact_prev = False
                    pen_button_down = False

            # ##############################################################################################
            # Side Buttons
            if n >= 13 :
                key_pressed = (data[11], data[12])
            else:
                key_pressed = (255, 51) # „brak klawisza”
            if(DEBUG) : print("--- key_pressed : " , key_pressed )

            pressed = button_map.get(key_pressed, None)

            if pressed is not None:
                press_type = 1
                pressed_prev = pressed
            elif key_pressed == (255, 51):
                press_type = 0
            else:
                # unknown/noisy tablet-button packet: ignore it, do not replay previous button
                press_type = 0
                pressed_prev = None

            if pressed_prev is not None:
                if(DEBUG) :
                    print("Key_pressed detected : ", pressed_prev , " :: ", config["actions"]["tablet_buttons"][pressed_prev] ,
                          " -- press type ( 0-up, 1-down , 2-hold) :", press_type )
                key_codes = config["actions"]["tablet_buttons"][pressed_prev].split("+")
                for key in key_codes:
                    act = ecodes.ecodes[key]
                    vbtn.write(ecodes.EV_KEY, act, press_type)

            # ##############################################################################################
            # BTN_STYLUS & BTN_STYLUS2
            if n >= 10:
                val = data[9]
                # check which button was pressed (0=lower, 1=upper) or None
                if   val == 4:  curr = 0
                elif val == 6:  curr = 1
                else:           curr = None

            # Event type: down (1) / up (0) / brak (None)
            if pen_pressed_prev is None and curr is not None:
                press_type = 1   # just pressed
                btn = curr
            elif pen_pressed_prev is not None and curr is None:
                press_type = 0   # just released
                btn = pen_pressed_prev
            else:
                press_type = None
                btn = None

            # If  event down or  up, send it to vpen
            if btn is not None and press_type is not None:
                codes = config["actions"]["pen_buttons"][btn].split("+")
                for key in codes:
                    code = ecodes.ecodes[key]
                    vpen.write(ecodes.EV_KEY, code, press_type)
                vpen.syn()

            # remember button state
            pen_pressed_prev = curr

            # Flush
            #vpen.syn()
            vbtn.syn()

            # reset pen button
            #pen_button=0x02

            # ---------------------------------------------------------------
    
        except usb.core.USBTimeoutError:
            # Second case of timeout (USBTimeoutError in my version of PyUSB)
            idle_timeouts += 1

            # Idle/timeout means: do not leave any pen button/tool state latched.
            vpen.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
            vpen.write(ecodes.EV_KEY, ecodes.BTN_TOOL_PEN, 0)
            vpen.syn()
            pen_contact_prev = False
            pen_button_down = False

            delay = min(0.05, 0.001 * idle_timeouts)

            if DEBUG and idle_timeouts % 60 == 0:
                print(f"[USB] idle timeout x{idle_timeouts} (delay={delay:.3f}s)")
            time.sleep(delay)
            if idle_timeouts >= 600:
                if DEBUG: print("[USB] long idle – soft re-claim iface 1")
                try: usb.util.release_interface(dev, 1)
                except Exception: pass
                try: usb.util.claim_interface(dev, 1)
                except usb.core.USBError: pass
                ep = dev[0].interfaces()[1].endpoints()[0]
                idle_timeouts = 0
            continue
    
        except usb.core.USBError as e:
            # Critical USB errors (ie. 19 after sleep/hibernation – no device)
            err = getattr(e, "errno", None)
            if err is None and e.args:
                err = e.args[0]
            if err == 19:
                if DEBUG: print("[USB] Device gone (Errno 19). Reconnecting…")
                try: usb.util.release_interface(dev, 1)
                except Exception: pass
                try: usb.util.dispose_resources(dev)
                except Exception: pass
                # lets wait while device will return
                new_dev = None
                for _ in range(150):  # ok. 15 s
                    time.sleep(0.1)
                    new_dev = usb.core.find(idVendor=config["vendor_id"], idProduct=config["product_id"])
                    if new_dev is not None:
                        break
                if new_dev is None:
                    if DEBUG: print("[USB] Not found again – exiting.")
                    try: vpen.close()
                    except Exception: pass
                    try: vbtn.close()
                    except Exception: pass
                    raise Exception("Device has been disconnected and not found again")
                # full init (setup_device_for_full_area)
                dev = new_dev
                time.sleep(0.1)  # a little wait after re-numeration
                ep = setup_device_for_full_area(dev)
                idle_timeouts = 0
                continue
            else:
                # Other errors
                if DEBUG: print(f"[USB] Unexpected USBError: {e!r}")
                raise
    
        except KeyboardInterrupt:
            vpen.close()
            vbtn.close()
            sys.exit("\nDriver terminated successfully.")
    
        except Exception as e:
            # Other error messages – lets log and try again
            if DEBUG: print("[MAIN] Unexpected exception:", e)
            time.sleep(0.05)
            continue
