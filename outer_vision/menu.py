"""Blink menu: the only way to reconfigure the instrument (no microphone, no hands).

  long blink on an object   -> "Left eye: new instrument. Right eye: new note. Both eyes: swap it."
  long blink on empty table -> "Left eye: slower. Right eye: faster. Both eyes: teach me a song."
                               (during a lesson, both eyes = stop the lesson)
  answer with a long blink of the matching eye(s); a double blink cancels; silence closes the menu.

Eye wallet (only when the owner's phone has authorised the headset, outer_vision/wallet):
  a song just ended         -> "Left eye: mint it. Right eye: send a tip. Both eyes: keep playing."
  send a tip                -> "Left eye: <contact 1>. Right eye: <contact 2>. Both eyes: cancel."
  a contact                 -> "Send 0.01 SOL to Mom? Both eyes to send." (a wink or silence = not sent)

Pure logic: speaking, OMNI requests and local actions go out through callbacks, so it's unit-testable.
Notes are not played while the menu is open (see `active`).
"""
from __future__ import annotations

PROMPTS = {
    "menu_object": "Left eye, new instrument. Right eye, new note. Both eyes, swap it.",
    "menu_space": "Left eye, slower. Right eye, faster. Both eyes, teach me a song.",
    "menu_space_lesson": "Left eye, slower. Right eye, faster. Both eyes, stop the lesson.",
    "swap_pick": "Look at the other one, and blink.",
    "swapped": "Swapped.",
    "cancelled": "Okay, never mind.",
    "lesson_stopped": "Lesson stopped.",
    "thinking": "One moment.",
    "menu_song": "Song saved. Left eye, mint it. Right eye, send a tip. Both eyes, keep playing.",
    "tip_pick": "Who gets the tip? Left eye or right eye. Both eyes, cancel.",
    "confirm_tip": "Send the tip? Both eyes to send.",
    "not_sent": "Okay, not sent.",
    "wallet_refused": "The wallet can't do that right now.",
}

OPTIONS = {   # shown on the overlay / state stream
    "object": {"left": "new instrument", "right": "new note", "both": "swap"},
    "space": {"left": "slower", "right": "faster", "both": "teach a song"},
    "space_lesson": {"left": "slower", "right": "faster", "both": "stop lesson"},
    "swap_pick": {"left": "swap with this", "right": "swap with this", "both": "swap with this"},
    "song": {"left": "mint it", "right": "send a tip", "both": "keep playing"},
    "tip_pick": {"left": "-", "right": "-", "both": "cancel"},
    "confirm": {"left": "cancel", "right": "cancel", "both": "send"},
}


class Menu:
    def __init__(self, cfg: dict, say, request, local, wallet=None):
        """say(prompt_key[, text]); request(command dict) -> OMNI; local(action dict) -> Music.apply;
        wallet: object with info() -> {"mint","tip","contacts","tip_sol"}, check_tip(label) -> reason|None,
        do(action) (runs it in the background and speaks the outcome)."""
        self.p = cfg["menu"]
        self.say, self.request, self.local, self.wallet = say, request, local, wallet
        self.state = None           # None | object | space | space_lesson | swap_pick | song | tip_pick | confirm
        self.focus = None           # object id the menu is about
        self.opened_at = 0.0
        self.song = None            # the song the song menu is about
        self.pending = None         # {"label", "tip_sol"} waiting for confirmation

    @property
    def active(self) -> bool:
        return self.state is not None

    def options(self):
        if self.state == "tip_pick":
            c = self.wallet.info()["contacts"]
            return {"left": c[0] if c else "-", "right": c[1] if len(c) > 1 else "-", "both": "cancel"}
        if self.state == "song":
            i = self.wallet.info()
            return {"left": "mint it" if i["mint"] else "-", "right": "send a tip" if i["tip"] else "-",
                    "both": "keep playing"}
        return OPTIONS.get(self.state)

    # ------------------------------------------------------------------ eye wallet
    def offer_song(self, song: dict, now: float) -> bool:
        """Called when a song is saved. Opens the song menu if the wallet can do anything with it."""
        if self.wallet is None or self.active:
            return False
        i = self.wallet.info()
        if not (i["mint"] or i["tip"]):
            return False
        self.song = song
        left = "mint it" if i["mint"] else "nothing"
        right = "send a tip" if i["tip"] else "nothing"
        self.state, self.focus, self.opened_at = "song", None, now
        self.say("menu_song", f"Song saved. Left eye, {left}. Right eye, {right}. Both eyes, keep playing.")
        return True

    def _wallet_gesture(self, side: str, focus, now: float):
        info = self.wallet.info()
        if self.state == "song":
            if side == "left" and info["mint"]:
                self._close()
                self.wallet.do({"type": "mint", "song": self.song})
            elif side == "right" and info["tip"]:
                c = info["contacts"]
                self.state, self.opened_at = "tip_pick", now
                names = f"Left eye, {c[0]}." + (f" Right eye, {c[1]}." if len(c) > 1 else "")
                self.say("tip_pick", f"Who gets {info['tip_sol']} SOL? {names} Both eyes, cancel.")
            elif side == "both":
                self._close()
            else:
                self.say("wallet_refused")
                self.opened_at = now
        elif self.state == "tip_pick":
            c = info["contacts"]
            label = c[0] if side == "left" and c else c[1] if side == "right" and len(c) > 1 else None
            if side == "both":
                self._close("not_sent")
                return
            if label is None:
                self.opened_at = now
                self.say("tip_pick", "Nobody there. " + (f"Left eye, {c[0]}. " if c else "") + "Both eyes, cancel.")
                return
            reason = self.wallet.check_tip(label)
            if reason:
                self._close()
                self.say("wallet_refused", f"Can't send it: {reason}.")
                return
            self.pending = {"label": label, "tip_sol": info["tip_sol"]}
            self.state, self.opened_at = "confirm", now
            self.say("confirm_tip", f"Send {info['tip_sol']} SOL to {label}? Both eyes to send.")
        elif self.state == "confirm":
            p, self.pending = self.pending, None
            if side == "both" and p:
                self._close()
                self.wallet.do({"type": "tip", "label": p["label"]})
            else:
                self._close("not_sent")      # only a deliberate both-eyes blink sends money

    def _open(self, state, focus, now, prompt):
        self.state, self.focus, self.opened_at = state, focus, now
        self.say(prompt)

    def _close(self, prompt=None):
        self.state = self.focus = None
        self.pending = None
        if prompt:
            self.say(prompt)

    def on_gesture(self, g: dict, focus, lesson_active: bool, now: float):
        """g: gesture from BlinkDetector; focus: object id under gaze (None = empty table)."""
        if g["kind"] == "double":
            if self.active:
                self._close("cancelled")
            return
        side = g["side"]
        if self.state in ("song", "tip_pick", "confirm"):
            self._wallet_gesture(side, focus, now)
            return
        if self.state is None:
            if focus is not None:
                self._open("object", focus, now, "menu_object")
            elif lesson_active:
                self._open("space_lesson", None, now, "menu_space_lesson")
            else:
                self._open("space", None, now, "menu_space")
        elif self.state == "object":
            target = self.focus
            if side == "both":
                self._open("swap_pick", target, now, "swap_pick")
                return
            self._close("thinking")
            self.request({"command": "change_instrument" if side == "left" else "change_note", "target": target})
        elif self.state in ("space", "space_lesson"):
            if side == "left":
                self._close("thinking")
                self.request({"command": "slower"})
            elif side == "right":
                self._close("thinking")
                self.request({"command": "faster"})
            elif self.state == "space_lesson":
                self.local({"type": "stop_lesson"})
                self._close("lesson_stopped")
            else:
                self._close("thinking")
                self.request({"command": "teach"})
        elif self.state == "swap_pick":
            if focus is None or focus == self.focus:
                self.say("swap_pick")            # still waiting for the other object
                self.opened_at = now
                return
            r = self.local({"type": "swap_notes", "a": self.focus, "b": focus})
            self._close("cancelled" if str(r).startswith("ignored") else "swapped")

    def tick(self, now: float):
        if self.active and now - self.opened_at > self.p["timeout_s"]:
            self._close("not_sent" if self.state == "confirm" else "cancelled")
