// Test rig only. Polls <rig>/cmd for one-line commands and answers in files.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';

const DIR = GLib.getenv('RIG_DIR');

export default class RigProbe {
    enable() {
        const seat = Clutter.get_default_backend().get_default_seat();
        this._ptr = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._kbd = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
            try { this._poll(); } catch (e) { this._say('err', String(e)); }
            return GLib.SOURCE_CONTINUE;
        });
        this._say('ready', 'ok');
    }
    disable() { if (this._id) GLib.source_remove(this._id); this._id = 0; }
    _say(name, text) {
        GLib.file_set_contents(`${DIR}/${name}.tmp`, text);
        GLib.rename(`${DIR}/${name}.tmp`, `${DIR}/${name}`);
    }
    _poll() {
        const path = `${DIR}/cmd`;
        if (!GLib.file_test(path, GLib.FileTest.EXISTS)) return;
        const [, bytes] = GLib.file_get_contents(path);
        GLib.unlink(path);
        const line = new TextDecoder().decode(bytes).trim();
        const [op, ...a] = line.split(' ');
        const now = () => GLib.get_monotonic_time();
        if (op === 'stack') {
            const out = global.get_window_actors().map(act => {
                const w = act.meta_window, r = w.get_frame_rect();
                return {title: w.get_title(), cls: w.get_wm_class(), type: w.get_window_type(),
                        layer: w.get_layer(), x11: w.get_client_type() === Meta.WindowClientType.X11,
                        focus: w.has_focus(), rect: [r.x, r.y, r.width, r.height]};
            });
            this._say('stack.json', JSON.stringify(out));
        } else if (op === 'shot') {
            const file = Gio.File.new_for_path(a[0]);
            const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
            new Shell.Screenshot().screenshot(false, stream, (o, res) => {
                try { o.screenshot_finish(res); stream.close(null); this._say('shot.done', a[0]); }
                catch (e) { this._say('err', String(e)); }
            });
        } else if (op === 'move') {
            this._ptr.notify_absolute_motion(now(), Number(a[0]), Number(a[1]));
            this._say('done', line);
        } else if (op === 'click') {       // click x y button(1|3)
            const btn = a[2] === '3' ? Clutter.BUTTON_SECONDARY : Clutter.BUTTON_PRIMARY;
            this._ptr.notify_absolute_motion(now(), Number(a[0]), Number(a[1]));
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 80, () => {
                this._ptr.notify_button(now(), btn, Clutter.ButtonState.PRESSED);
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 60, () => {
                    this._ptr.notify_button(now(), btn, Clutter.ButtonState.RELEASED);
                    this._say('done', line);
                    return GLib.SOURCE_REMOVE;
                });
                return GLib.SOURCE_REMOVE;
            });
        } else if (op === 'key') {         // key <keyval>
            const kv = Number(a[0]);
            this._kbd.notify_keyval(now(), kv, Clutter.KeyState.PRESSED);
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 60, () => {
                this._kbd.notify_keyval(now(), kv, Clutter.KeyState.RELEASED);
                this._say('done', line);
                return GLib.SOURCE_REMOVE;
            });
        } else {
            this._say('err', `unknown: ${line}`);
        }
    }
}
