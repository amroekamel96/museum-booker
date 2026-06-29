"""
Playwright automation for museiitaliani.it → portale.museiitaliani.it

Confirmed booking flow (Pantheon + generic portal museums):
  1.  museiitaliani.it/acquista-biglietto  →  type name, click Cerca
  2.  Click museum card  (a.card-title.museo-title)
  3.  Lands on portale.museiitaliani.it/b2c/buyTicketless/UUID
  4.  Click AcquistaBiglietti card
  5.  Select ticket offer (SINGOLI / GRUPPI / Visite guidate …)
  6.  Calendar: navigate to target month, click the date
  7.  Time slot: click matching badge (or first available)
  8.  Quantities (only for non-calendar offers): set counter-input per type
  9.  Click forward-btn  ("Prosegui" / "Acquista N biglietti per €X")
  10. Guest modal: click "Prosegui come ospite" (force=True)
  11. Visitor form: fill name / lastname / email / confirmEmail per ticket
  12. Click forward-btn again  →  payment page (left to user)
"""

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from playwright.sync_api import (
    Page,
    Locator,
    sync_playwright,
    TimeoutError as PWTimeout,
)

os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path.home() / ".museum-booker" / "browsers"),
)

SEARCH_URL = "https://www.museiitaliani.it/acquista-biglietto"

# Maps user-facing category labels → portal offer name substrings
OFFER_KEYWORDS: dict[str, list[str]] = {
    "Individuals": ["SINGOLI", "Individuale", "Individual", "Singolo"],
    "Groups":      ["GRUPPI",  "Gruppo",      "Group"],
    "Guided tour": ["Visita guidata", "Visite guidate", "Guided tour",
                    "Guidata", "Guided"],
}

# Maps user-facing ticket type labels → portal .ticket-type text
TICKET_TYPE_LABELS: dict[str, list[str]] = {
    "Full price":  ["Intero",    "Full price", "Full",    "Adulto",  "Normale"],
    "Reduced":     ["Ridotto",   "Reduced",    "Agevolato"],
    "Free":        ["Gratuito",  "Free",       "Omaggio"],
    "Tour Leader": ["Capogruppo","Tour Leader","Accompagnatore"],
}


class MuseumBooker:
    def __init__(self, status_callback: Callable[[str], None] | None = None):
        self.log = status_callback or print
        self.page: Page | None = None

    # ------------------------------------------------------------------ public

    def run(self, data: dict):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=500)
            context = browser.new_context(locale="it-IT")
            self.page = context.new_page()
            self.page.set_default_timeout(25_000)

            try:
                self._search_museum(data["museum"])
                self._click_biglietti()
                self._select_offer(data["category"])
                self._pick_date_and_slot(data["date"], data.get("timeslot", ""))
                self._click_forward()          # "Prosegui" from calendar to quantities
                self._set_quantities(data["quantities"], visitor_count=len(data.get("visitors", [])))
                self._click_forward()          # "Acquista N biglietti" or forward from quantities
                self._guest_checkout()         # click "Prosegui come ospite"
                self._fill_visitors(data["visitors"])
                self._click_forward()          # proceed to payment
                self.log("Booking submitted — please complete the payment in the browser.")
            except Exception as exc:
                self.log(f"Booking failed: {exc}")
                raise
            finally:
                self.log("Browser will close in 20 seconds.")
                time.sleep(20)
                browser.close()

    # ------------------------------------------------------------------ steps

    def _search_museum(self, name: str):
        self.log("Opening booking portal…")
        self.page.goto(SEARCH_URL, wait_until="networkidle", timeout=30_000)
        time.sleep(1)

        # Dismiss cookie banner
        try:
            self.page.locator(".cookiebar-confirm").click(timeout=3_000)
        except PWTimeout:
            pass

        self.log(f"Searching for '{name}'…")
        self.page.locator("input[aria-label='Cerca per nome']").fill(name)
        self.page.locator("button.btn-primary:has-text('Cerca')").first.click()
        self.page.wait_for_load_state("networkidle")
        time.sleep(1.5)

        # Click matching card
        card = self.page.locator(f"a.card-title.museo-title:has-text('{name}')")
        if card.count():
            self.log(f"Clicking card: {card.first.inner_text().strip()!r}")
            card.first.click()
        else:
            # Fallback: first card in results
            all_cards = self.page.locator("a.card-title.museo-title")
            if all_cards.count():
                first_name = all_cards.first.inner_text().strip()
                self.log(f"Exact match not found; clicking first result: {first_name!r}")
                all_cards.first.click()
            else:
                raise RuntimeError(f"No museum cards found for '{name}'")

        self.page.wait_for_load_state("networkidle")
        time.sleep(2)
        self.log(f"Reached: {self.page.url}")

    def _click_biglietti(self):
        self.log("Clicking 'Biglietti' purchase card…")
        self.page.locator("[aria-label='AcquistaBiglietti']").click(force=True)
        time.sleep(2)

    def _select_offer(self, category: str):
        self.log(f"Selecting offer for category: {category!r}…")
        keywords = OFFER_KEYWORDS.get(category, [category])

        offers = self.page.locator("button.btn-wrapper")
        if not offers.count():
            self.log("No offer buttons found — continuing.")
            return

        # Try to match by keyword
        for keyword in keywords:
            for i in range(offers.count()):
                aria = offers.nth(i).get_attribute("aria-label") or ""
                if keyword.lower() in aria.lower():
                    label = aria.split(" - ")[0].replace("Nome offerta: : ", "")
                    self.log(f"Selecting offer: {label!r}")
                    offers.nth(i).click()
                    time.sleep(2)
                    return

        # Fallback: skip free/Sunday offers, pick first paid
        for i in range(offers.count()):
            aria = offers.nth(i).get_attribute("aria-label") or ""
            if "domenica" not in aria.lower() and "gratuito" not in aria.lower():
                label = aria.split(" - ")[0].replace("Nome offerta: : ", "")
                self.log(f"No match for {category!r}; selecting: {label!r}")
                offers.nth(i).click()
                time.sleep(2)
                return

        self.log("Warning: could not select an offer — clicking first one.")
        offers.first.click()
        time.sleep(2)

    def _pick_date_and_slot(self, date_str: str, timeslot: str):
        """Handle the calendar + time-slot section (if present)."""
        # Check if a calendar is present
        calendar = self.page.locator(".calendar-section, app-calendar, .calendar-container")
        if not calendar.count():
            self.log("No calendar found — skipping date/time selection.")
            return

        self.log(f"Selecting date: {date_str}…")
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self.log(f"Invalid date format: {date_str!r} — expected YYYY-MM-DD")
            return

        self._navigate_to_month(target)
        self._click_calendar_day(target)
        time.sleep(1.5)
        self._pick_timeslot(timeslot)

    def _navigate_to_month(self, target: datetime):
        """Advance (or rewind) the calendar to the target month."""
        for _ in range(24):  # safety cap
            month_el = self.page.locator("span.month-name")
            if not month_el.count():
                break
            text = month_el.first.get_attribute("aria-label") or month_el.first.inner_text()
            # text like "Mese selezionato Maggio 2026" or "Maggio 2026"
            for fmt in ("%B %Y", "Mese selezionato %B %Y"):
                try:
                    current = datetime.strptime(text.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                break  # couldn't parse — stop

            diff = (target.year - current.year) * 12 + (target.month - current.month)
            if diff == 0:
                break
            btn_label = "Mese successivo" if diff > 0 else "Mese precedente"
            nav = self.page.locator(f"button.change-month-btn[aria-label='{btn_label}']")
            if not nav.count():
                break
            nav.first.click()
            time.sleep(0.6)

    def _click_calendar_day(self, target: datetime):
        """Click the day cell matching the target date."""
        date_label = target.strftime("%d/%m/%Y")

        # Prefer exact aria-labelledby match
        day = self.page.locator(f"time[aria-labelledby='{date_label}'][role='button']")
        if day.count():
            cls = day.first.get_attribute("class") or ""
            if "not-in-program" in cls or "closure" in cls:
                self.log(f"Warning: {date_label} is closed or not in program — falling back to first available day.")
            else:
                self.log(f"Clicking date: {date_label}")
                day.first.click()
                return

        # Fallback: click first available day
        avail = self.page.locator("time.day.available[role='button']")
        if avail.count():
            fb_label = avail.first.get_attribute("aria-labelledby") or "?"
            self.log(f"Exact date not found; clicking first available day: {fb_label}")
            avail.first.click()
        else:
            self.log("Warning: no available calendar days found.")

    def _pick_timeslot(self, timeslot: str):
        """Click the time slot badge matching the requested time (or first available)."""
        # Wait for time slot section to load
        ts_section = self.page.locator(".timeslot-section")
        try:
            ts_section.wait_for(state="visible", timeout=8_000)
        except PWTimeout:
            pass

        # All non-disabled time-slot badges
        badges = self.page.locator("button[class*='time']:not(.disabled):not([disabled])")
        if not badges.count():
            self.log("No available time slots found.")
            return

        if timeslot:
            self.log(f"Looking for time slot: {timeslot!r}…")
            # Try partial match on slot text  (e.g. "13:00" matches "13:00-14:00")
            for i in range(badges.count()):
                btn_text = badges.nth(i).inner_text().strip()
                if timeslot in btn_text:
                    self.log(f"Clicking time slot: {btn_text!r}")
                    badges.nth(i).click()
                    time.sleep(1)
                    return
            self.log(f"Time slot {timeslot!r} not found; picking first available.")

        first_text = badges.first.inner_text().strip()
        self.log(f"Selecting time slot: {first_text!r}")
        badges.first.click()
        time.sleep(1)

    def _set_quantities(self, quantities: dict[str, int], visitor_count: int = 0):
        """Set ticket quantities using +/- counter buttons."""
        ticket_cards = self.page.locator("single-ticket-selector")
        try:
            ticket_cards.first.wait_for(state="visible", timeout=8_000)
        except PWTimeout:
            self.log("No quantity controls found — skipping.")
            return

        has_explicit = any(v > 0 for v in quantities.values())
        if not has_explicit and visitor_count > 0:
            quantities = {"Full price": visitor_count}
            self.log(f"No explicit quantities — defaulting to {visitor_count} Full price ticket(s).")

        self.log("Setting ticket quantities…")
        for ticket_type, qty in quantities.items():
            if qty <= 0:
                continue
            labels = TICKET_TYPE_LABELS.get(ticket_type, [ticket_type])
            idx = self._find_ticket_card(ticket_cards, labels)
            if idx is None:
                self.log(f"  Ticket type {ticket_type!r} not found on page — skipping.")
                continue

            card = ticket_cards.nth(idx)
            counter_input = card.locator("input.counter-input")
            add_btn = card.locator("button[aria-label='Add a ticket'], "
                                   "button[aria-label='Aggiugni un biglietto'], "
                                   "button[aria-label='Aggiungi un biglietto']")

            self.log(f"  {ticket_type}: {qty}")
            if counter_input.count():
                counter_input.first.fill(str(qty))
                counter_input.first.press("Tab")
                time.sleep(0.5)
            elif add_btn.count():
                for _ in range(qty):
                    add_btn.first.click()
                    time.sleep(0.3)

    def _find_ticket_card(self, cards_locator: Locator, labels: list[str]) -> int | None:
        for i in range(cards_locator.count()):
            cell_text = cards_locator.nth(i).inner_text().strip()
            for label in labels:
                if label.lower() in cell_text.lower():
                    return i
        return None

    def _click_forward(self):
        """Click the forward/continue button (forward-btn class)."""
        fwd = self.page.locator("button.forward-btn:not([disabled])")
        try:
            fwd.first.wait_for(state="visible", timeout=8_000)
        except PWTimeout:
            self.log("Forward button not visible — trying Prosegui fallback.")
            alt = self.page.locator("button:has-text('Prosegui'):not([disabled]):not(.modal-btn)")
            if alt.count():
                alt.first.click()
                time.sleep(2)
                return
            raise RuntimeError("No forward/continue button found.")

        label = fwd.first.inner_text().strip()
        self.log(f"Clicking: {label!r}…")
        fwd.first.click()
        time.sleep(2)

    def _guest_checkout(self):
        """Handle the 'Prosegui come ospite' modal (if it appears)."""
        guest = self.page.locator("button.modal-btn.guest-payment, button:has-text('Prosegui come ospite'), button:has-text('Continue as guest')")
        try:
            guest.first.wait_for(state="visible", timeout=6_000)
        except PWTimeout:
            self.log("Guest checkout modal not present — user may already be logged in.")
            return
        self.log("Choosing guest checkout…")
        guest.first.click(force=True)
        time.sleep(2)

    def _fill_visitors(self, visitors: list[dict]):
        self.log(f"Filling details for {len(visitors)} visitor(s)…")
        time.sleep(1)

        # Expand collapsed accordion sections before looking for inputs
        expand_btns = self.page.locator("button.expand-form-btn")
        try:
            expand_btns.first.wait_for(state="visible", timeout=10_000)
        except PWTimeout:
            self.log("Visitor form not found — cannot fill visitor details automatically.")
            return
        for i in range(expand_btns.count()):
            expand_btns.nth(i).click()
            time.sleep(0.4)
        time.sleep(0.5)

        name_inputs    = self.page.locator("input[formcontrolname='name'][placeholder='Nome']")
        last_inputs    = self.page.locator("input[formcontrolname='lastname'][placeholder='Cognome']")
        email_inputs   = self.page.locator("input[formcontrolname='email'][placeholder='Email']")
        confirm_inputs = self.page.locator("input[formcontrolname='confirmEmail']")

        # Wait for inputs to be visible after expanding
        try:
            name_inputs.first.wait_for(state="visible", timeout=5_000)
        except PWTimeout:
            self.log("Visitor inputs not visible after expanding — cannot fill automatically.")
            return

        for i, visitor in enumerate(visitors):
            # If we need more sections than are visible, try "Aggiungi visitatore"
            if i >= name_inputs.count():
                add_v = self.page.locator(
                    "button:has-text('Aggiungi'), button:has-text('Add visitor'), "
                    "button[aria-label*='Aggiungi visitatore']"
                )
                if add_v.count():
                    add_v.first.click()
                    time.sleep(1)
                else:
                    self.log(f"  Cannot add visitor {i + 1}: no 'Add visitor' button found.")
                    break

            self.log(f"  Visitor {i + 1}: {visitor['first_name']} {visitor['last_name']}")
            name_inputs.nth(i).fill(visitor["first_name"])
            last_inputs.nth(i).fill(visitor["last_name"])
            email_inputs.nth(i).fill(visitor["email"])
            if i < confirm_inputs.count():
                confirm_inputs.nth(i).fill(visitor["email"])
            time.sleep(0.2)

        self.log("Visitor details filled.")
