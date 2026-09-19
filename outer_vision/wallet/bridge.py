"""Connects the blink menu to the wallet: quick checks inline, network work in the background, and every
outcome spoken, so the player always hears whether money moved."""
from __future__ import annotations

import threading

from .delegation import DelegationError, sol
from .policy import WalletError


class MenuBridge:
    def __init__(self, wallet, speak, background=True):
        """speak(text) blocks until spoken; background=False runs actions inline (tests)."""
        self.wallet, self.speak, self.background = wallet, speak, background
        self.busy = threading.Lock()

    def info(self) -> dict:
        w = self.wallet
        if not w.active():
            return {"mint": False, "tip": False, "contacts": [], "tip_sol": "0"}
        d = w.delegation
        return {"mint": "mint" in d.actions, "tip": "tip" in d.actions,
                "contacts": [c["label"] for c in d.contacts], "tip_sol": sol(d.tip_lamports)}

    def check_tip(self, label: str):
        try:
            self.wallet.check_tip(label)
            return None
        except WalletError as e:
            return str(e)

    def do(self, action: dict):
        if self.background:
            threading.Thread(target=self._run, args=(action,), daemon=True).start()
        else:
            self._run(action)

    def _run(self, action: dict):
        if not self.busy.acquire(blocking=False):
            self.speak("The wallet is still busy with the last one.")
            return
        try:
            if action["type"] == "mint":
                self.speak("Minting your song.")
                self.wallet.mint(action["song"])
                self.speak("Minted. The song is in your wallet.")
            elif action["type"] == "tip":
                self.speak("Sending.")
                e = self.wallet.tip(action["label"])
                self.speak(f"Sent {sol(e['lamports'])} SOL to {action['label']}.")
        except (WalletError, DelegationError) as e:
            self.speak(f"Not {'minted' if action['type'] == 'mint' else 'sent'}: {e}.")
        except Exception as e:                        # never leave the player without an answer
            self.wallet.log(f"[wallet] {action['type']} crashed: {e!r}")
            self.speak("Something went wrong. Nothing was sent.")
        finally:
            self.busy.release()
