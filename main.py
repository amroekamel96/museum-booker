import subprocess
import sys
import threading
import csv
from datetime import date, datetime
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from booker import MuseumBooker


def _ensure_browser():
    """Download Playwright's Chromium on first run (silent, no console flash)."""
    marker = Path.home() / ".museum-booker" / "browser_ready"
    if marker.exists():
        return
    try:
        from playwright._impl._driver import compute_driver_executable
        driver = compute_driver_executable()
        kwargs = {"capture_output": True}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.run([str(driver), "install", "chromium"], **kwargs)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except Exception:
        pass

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

        # Quantities — only used for non-calendar offers (Anfiteatro style)
        # For the Pantheon the visitor count sets the quantity automatically
        qty_note = ctk.CTkLabel(
            p,
            text="Ticket quantities (for non-calendar museums only —\n"
                 "Pantheon derives count from the visitors list below)",
            font=ctk.CTkFont(weight="bold"), anchor="w", wraplength=320,
            justify="left")
        qty_note.grid(row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))
        r += 1

        self.qty_vars: dict[str, ctk.IntVar] = {}
        for ticket_type in ("Full price", "Reduced", "Free", "Tour Leader"):
            ctk.CTkLabel(p, text=ticket_type, anchor="w").grid(
                row=r, column=0, sticky="w", padx=12, pady=3)
            var = ctk.IntVar(value=0)
            self.qty_vars[ticket_type] = var
            ctk.CTkEntry(p, textvariable=var, width=70).grid(
                row=r, column=1, sticky="e", padx=12, pady=3)
            r += 1

        # Visitors header
        ctk.CTkLabel(p, text="Visitors",
                     font=ctk.CTkFont(weight="bold"), anchor="w").grid(
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
        for text, w in (("First name", 130), ("Last name", 130), ("Email", 180)):
            ctk.CTkLabel(hdr, text=text, width=w, anchor="w").pack(side="left", padx=2)

    def _add_visitor_row(self, first="", last="", email=""):
        row_frame = ctk.CTkFrame(self.visitor_container, fg_color="transparent")
        row_frame.pack(fill="x", pady=2)

        v_first = ctk.StringVar(value=first)
        v_last = ctk.StringVar(value=last)
        v_email = ctk.StringVar(value=email)

        ctk.CTkEntry(row_frame, textvariable=v_first, width=130,
                     placeholder_text="First").pack(side="left", padx=2)
        ctk.CTkEntry(row_frame, textvariable=v_last, width=130,
                     placeholder_text="Last").pack(side="left", padx=2)
        ctk.CTkEntry(row_frame, textvariable=v_email, width=180,
                     placeholder_text="email@example.com").pack(side="left", padx=2)
        ctk.CTkButton(
            row_frame, text="x", width=28, height=28,
            fg_color="#c0392b", hover_color="#e74c3c",
            command=lambda f=row_frame: self._remove_visitor_row(f)
        ).pack(side="left", padx=(4, 0))

        self._visitor_rows.append((row_frame, v_first, v_last, v_email))

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
                for row in csv.DictReader(fh):
                    self._add_visitor_row(
                        row.get("first_name") or row.get("First Name", ""),
                        row.get("last_name") or row.get("Last Name", ""),
                        row.get("email") or row.get("Email", ""),
                    )
            self._log(f"Imported visitors from {path}")
        except Exception as exc:
            messagebox.showerror("CSV Error", str(exc))

    def _get_visitors(self) -> list[dict]:
        result = []
        for _, v_first, v_last, v_email in self._visitor_rows:
            first = v_first.get().strip()
            last = v_last.get().strip()
            email = v_email.get().strip()
            if first or last or email:
                result.append({"first_name": first, "last_name": last, "email": email})
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

        data = {
            "museum": self.museum_var.get().strip(),
            "date": self.date_var.get().strip(),
            "timeslot": self.timeslot_var.get().strip(),
            "category": self.category_var.get(),
            "quantities": {k: v.get() for k, v in self.qty_vars.items()},
            "visitors": self._get_visitors(),
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
