import subprocess
import sys
import threading
import csv
from datetime import date, datetime
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from booker import MuseumBooker


def _get_browsers_path() -> str:
    """Return the path where Playwright should look for browsers."""
    import os
    # Check for bundled browsers next to the executable (PyInstaller build)
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / "browsers"
        if bundled.exists():
            return str(bundled)
    # Fallback to user home directory
    return str(Path.home() / ".museum-booker" / "browsers")


def _ensure_browser():
    """Set up Playwright browser path. Bundled builds already include Chromium."""
    import os
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _get_browsers_path()

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


class MuseumBookerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Museum Booker")
        self.geometry("1000x780")
        self.minsize(800, 600)

        self._visitor_rows: list[tuple] = []
        self.booking_thread: threading.Thread | None = None

        self._build_ui()

    # ------------------------------------------------------------------ layout

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        outer = ctk.CTkFrame(self)
        outer.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        outer.grid_columnconfigure(0, weight=3)
        outer.grid_columnconfigure(1, weight=2)
        outer.grid_rowconfigure(0, weight=1)

        form_scroll = ctk.CTkScrollableFrame(outer, label_text="Booking Details")
        form_scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        form_scroll.grid_columnconfigure(1, weight=1)

        log_panel = ctk.CTkFrame(outer)
        log_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        log_panel.grid_columnconfigure(0, weight=1)
        log_panel.grid_rowconfigure(1, weight=1)

        self._build_form(form_scroll)
        self._build_log(log_panel)

    # ------------------------------------------------------------------ form

    def _build_form(self, p):
        r = 0

        # Museum name — pre-filled with the Pantheon
        ctk.CTkLabel(p, text="Museum name", anchor="w").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(14, 2))
        r += 1
        self.museum_var = ctk.StringVar(
            value="Pantheon e Basilica di Santa Maria ad Martyres")
        ctk.CTkEntry(p, textvariable=self.museum_var).grid(
            row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        r += 1

        # Visit date
        ctk.CTkLabel(p, text="Visit date (YYYY-MM-DD)", anchor="w").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 2))
        r += 1
        self.date_var = ctk.StringVar(value=date.today().strftime("%Y-%m-%d"))
        ctk.CTkEntry(p, textvariable=self.date_var).grid(
            row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        r += 1

        # Time slot
        ctk.CTkLabel(p, text="Preferred time slot (e.g. 10:00 — leave blank to pick first available)",
                     anchor="w", wraplength=320).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 2))
        r += 1
        self.timeslot_var = ctk.StringVar()
        ctk.CTkEntry(p, textvariable=self.timeslot_var,
                     placeholder_text="10:00").grid(
            row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        r += 1

        # Category — maps to portal offer names
        ctk.CTkLabel(p, text="Ticket category", anchor="w").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 2))
        r += 1
        self.category_var = ctk.StringVar(value="Individuals")
        ctk.CTkOptionMenu(p, variable=self.category_var,
                          values=["Individuals", "Groups", "Guided tour"]).grid(
            row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        r += 1

        # Shared email
        ctk.CTkLabel(p, text="Email (used for all visitors)", anchor="w").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 2))
        r += 1
        self.email_var = ctk.StringVar()
        ctk.CTkEntry(p, textvariable=self.email_var,
                     placeholder_text="email@example.com").grid(
            row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        r += 1

        # Visitors header
        ctk.CTkLabel(p, text="Visitors (prefix name with 'Kid' for free tickets)",
                     font=ctk.CTkFont(weight="bold"), anchor="w",
                     wraplength=320, justify="left").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(14, 4))
        r += 1

        btns = ctk.CTkFrame(p, fg_color="transparent")
        btns.grid(row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 6))
        ctk.CTkButton(btns, text="+ Add visitor", command=self._add_visitor_row,
                      width=120).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="Import CSV", command=self._import_csv,
                      width=120).pack(side="left")
        r += 1

        self.visitor_container = ctk.CTkFrame(p, fg_color="transparent")
        self.visitor_container.grid(row=r, column=0, columnspan=2,
                                    sticky="ew", padx=12, pady=(0, 10))
        r += 1
        self._add_visitor_header()

        # Start button
        self.start_btn = ctk.CTkButton(
            p, text="Start Booking",
            command=self._start_booking,
            height=46, font=ctk.CTkFont(size=16, weight="bold"))
        self.start_btn.grid(row=r, column=0, columnspan=2,
                            sticky="ew", padx=12, pady=(10, 16))

    # ------------------------------------------------------------------ log panel

    def _build_log(self, p):
        ctk.CTkLabel(p, text="Live status",
                     font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        self.log_box = ctk.CTkTextbox(p, state="disabled", wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 6))
        ctk.CTkButton(p, text="Clear", command=self._clear_log,
                      width=80, height=28).grid(
            row=2, column=0, pady=(0, 10))

    # ------------------------------------------------------------------ visitors

    def _add_visitor_header(self):
        hdr = ctk.CTkFrame(self.visitor_container, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 2))
        for text, w in (("Name", 220), ("Last name", 130), ("Type", 60)):
            ctk.CTkLabel(hdr, text=text, width=w, anchor="w").pack(side="left", padx=2)

    def _add_visitor_row(self, first="", last=".", is_kid=False):
        row_frame = ctk.CTkFrame(self.visitor_container, fg_color="transparent")
        row_frame.pack(fill="x", pady=2)

        v_first = ctk.StringVar(value=first)
        v_last = ctk.StringVar(value=last)
        v_kid = ctk.BooleanVar(value=is_kid)

        ctk.CTkEntry(row_frame, textvariable=v_first, width=220,
                     placeholder_text="Full name").pack(side="left", padx=2)
        ctk.CTkEntry(row_frame, textvariable=v_last, width=130,
                     placeholder_text=".").pack(side="left", padx=2)
        ctk.CTkCheckBox(row_frame, text="Kid", variable=v_kid,
                        width=60).pack(side="left", padx=2)
        ctk.CTkButton(
            row_frame, text="x", width=28, height=28,
            fg_color="#c0392b", hover_color="#e74c3c",
            command=lambda f=row_frame: self._remove_visitor_row(f)
        ).pack(side="left", padx=(4, 0))

        self._visitor_rows.append((row_frame, v_first, v_last, v_kid))

    def _remove_visitor_row(self, frame):
        self._visitor_rows = [
            (f, a, b, c) for f, a, b, c in self._visitor_rows if f is not frame
        ]
        frame.destroy()

    def _import_csv(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                for line in fh:
                    name = line.strip()
                    if not name:
                        continue
                    is_kid = name.lower().startswith("kid ")
                    if is_kid:
                        name = name[4:].strip()
                    self._add_visitor_row(name, ".", is_kid)

            self._log(f"Imported visitors from {path}")
        except Exception as exc:
            messagebox.showerror("CSV Error", str(exc))

    def _get_visitors(self) -> list[dict]:
        email = self.email_var.get().strip()
        result = []
        for _, v_first, v_last, v_kid in self._visitor_rows:
            first = v_first.get().strip()
            last = v_last.get().strip()
            if first or last:
                result.append({
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "is_kid": v_kid.get(),
                })
        return result

    # ------------------------------------------------------------------ booking

    def _validate(self) -> bool:
        if not self.museum_var.get().strip():
            messagebox.showwarning("Validation", "Please enter a museum name.")
            return False
        if not self.date_var.get().strip():
            messagebox.showwarning("Validation", "Please enter a visit date.")
            return False
        if not self._get_visitors():
            messagebox.showwarning("Validation", "Please add at least one visitor.")
            return False
        return True

    def _start_booking(self):
        if not self._validate():
            return

        visitors = self._get_visitors()
        adults = [v for v in visitors if not v["is_kid"]]
        kids = [v for v in visitors if v["is_kid"]]

        data = {
            "museum": self.museum_var.get().strip(),
            "date": self.date_var.get().strip(),
            "timeslot": self.timeslot_var.get().strip(),
            "category": self.category_var.get(),
            "quantities": {"Full price": len(adults), "Free": len(kids)},
            "visitors": adults + kids,
        }

        self.start_btn.configure(state="disabled", text="Booking in progress…")
        self._log("Starting booking process…")

        self.booking_thread = threading.Thread(
            target=self._run_booking, args=(data,), daemon=True)
        self.booking_thread.start()

    def _run_booking(self, data: dict):
        try:
            booker = MuseumBooker(status_callback=self._log)
            booker.run(data)
        except Exception as exc:
            self._log(f"ERROR: {exc}")
        finally:
            self.after(0, lambda: self.start_btn.configure(
                state="normal", text="Start Booking"))

    # ------------------------------------------------------------------ log helpers

    def _log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.after(0, self._append_log, f"[{ts}] {message}")

    def _append_log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")


if __name__ == "__main__":
    _ensure_browser()
    app = MuseumBookerApp()
    app.mainloop()
