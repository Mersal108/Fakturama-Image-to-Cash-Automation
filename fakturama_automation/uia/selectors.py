"""Every Fakturama UI string the automation depends on, in one place.

Keeping these together is deliberate.  The flow logic below never hard-codes a
caption, so adapting to a different Fakturama build, language pack or version
is an edit to this file rather than a hunt through the automation.

Each entry is a tuple of acceptable captions, tried in order.  That absorbs the
small wording differences between releases (``terms of payment`` vs
``Terms of payment``) without weakening the exact-match rule the brief requires
for *data* comparisons -- these are UI chrome, not business values.
"""

from __future__ import annotations

from typing import Sequence, Tuple

# --- windows ---------------------------------------------------------------

MAIN_WINDOW_TITLE_RE = r".*Fakturama.*"

# --- top toolbar -----------------------------------------------------------

# Fakturama's toolbar items expose their *tooltip* as the accessible name, not
# the short caption painted beside the icon.  The tooltip is listed first
# because it is what UIA actually reports on 2.2.0; the shorter forms remain as
# fallbacks for other builds and language packs.
TOOLBAR_ORDER: Tuple[str, ...] = (
    "Create: New Order", "Order", "Auftrag",
)
TOOLBAR_INVOICE: Tuple[str, ...] = (
    "Create: New Invoice", "Invoice", "Rechnung",
)
TOOLBAR_SAVE: Tuple[str, ...] = (
    "Save the current contents", "Save", "Speichern",
)
TOOLBAR_PRODUCT: Tuple[str, ...] = (
    "Create a new product", "Product", "Produkt",
)
TOOLBAR_CONTACT: Tuple[str, ...] = (
    "Create a new contact", "Contact", "Kontakt",
)

# --- left navigation -------------------------------------------------------

NAV_NEW_CONTACT: Tuple[str, ...] = ("New Contact", "Neuer Kontakt")
NAV_NEW_PRODUCT: Tuple[str, ...] = ("New product", "New Product", "Neues Produkt")
NAV_DOCUMENTS: Tuple[str, ...] = ("Documents", "Dokumente")
NAV_VATS: Tuple[str, ...] = ("VATs", "VAT", "Umsatzsteuer")
NAV_TERMS_OF_PAYMENT: Tuple[str, ...] = (
    "terms of payment", "Terms of payment", "Zahlungsbedingungen",
)
NAV_PRODUCTS: Tuple[str, ...] = ("Products", "Produkte")
NAV_DEBTORS: Tuple[str, ...] = ("Debtors", "Debitoren")

# --- order / invoice editor ------------------------------------------------

LBL_NUMBER: Tuple[str, ...] = ("No.", "Nr.")
LBL_DATE: Tuple[str, ...] = ("Date", "Datum")
LBL_CUSTOMER_REF: Tuple[str, ...] = ("Cust.Ref.", "CustRef", "Kunden-Ref.")
LBL_SERVICE_DATE: Tuple[str, ...] = ("Service date", "Leistungsdatum")
LBL_ORDER_DATE: Tuple[str, ...] = ("Order Date", "Bestelldatum")
LBL_CONSULTANT: Tuple[str, ...] = ("Consultant", "Berater")
LBL_SHIPPING: Tuple[str, ...] = ("Shipping", "Versand")
LBL_DISCOUNT: Tuple[str, ...] = ("Discount", "Rabatt")
LBL_TOTAL_NET: Tuple[str, ...] = ("Total Net", "Summe netto")
LBL_VAT: Tuple[str, ...] = ("VAT", "MwSt.")
LBL_TOTAL: Tuple[str, ...] = ("Total", "Summe")
LBL_ADDRESSES: Tuple[str, ...] = ("Addresses", "Adressen")
LBL_INVOICE_ADDRESS: Tuple[str, ...] = ("Invoice address", "Rechnungsadresse")
LBL_DELIVERY_ADDRESS: Tuple[str, ...] = ("Delivery address", "Lieferadresse")

# Document price mode: "net"/"gross" and "with VAT"/"without VAT".
OPT_NET: Tuple[str, ...] = ("net", "Netto")
OPT_WITH_VAT: Tuple[str, ...] = ("with VAT", "With VAT", "mit MwSt.")

FOLLOW_UP_AREA: Tuple[str, ...] = (
    "Create a follow-up document", "Folgedokument erstellen",
)
FOLLOW_UP_INVOICE: Tuple[str, ...] = ("Invoice", "Rechnung")

# --- list-view "create a new entry" buttons --------------------------------
#
# Each list view's add button carries a descriptive accessible name, which is a
# far safer handle than "the leftmost icon near the search box": the Documents,
# Shippings and terms-of-payment views share one tab stack and place their
# toolbars at identical coordinates, so position alone cannot tell them apart.
BTN_NEW_PAYMENT_TERM: Tuple[str, ...] = (
    "Create a new term of payment", "Neue Zahlungsbedingung anlegen",
)
BTN_NEW_VAT: Tuple[str, ...] = (
    "Create a new tax rate", "Create a new VAT", "Neue Umsatzsteuer anlegen",
)

# Every list view's add button is captioned this way, so a build that renames
# one can still be driven without a new entry here.
BTN_NEW_PREFIXES: Tuple[str, ...] = ("create a new", "create:", "neue", "neuen")
BTN_NEW_SHIPPING: Tuple[str, ...] = (
    "Create a new shipping method", "Neue Versandart anlegen",
)

# --- selector dialogs ------------------------------------------------------

DLG_SELECT_ADDRESS: Tuple[str, ...] = ("Select the address", "Adresse auswählen")
DLG_SELECT_PRODUCT: Tuple[str, ...] = ("Select a product", "Produkt auswählen")
DLG_SEARCH_FIELD: Tuple[str, ...] = ("Search", "Suche", "Suchen")
BTN_OK: Tuple[str, ...] = ("OK", "Ok")
BTN_CANCEL: Tuple[str, ...] = ("Cancel", "Abbrechen")

# --- contact (debtor) editor ----------------------------------------------

LBL_CUSTOMER_ID: Tuple[str, ...] = ("Customer ID", "Kundennummer")
LBL_COMPANY: Tuple[str, ...] = ("Company", "Firma")
LBL_SALUTATION: Tuple[str, ...] = ("Salutation", "Anrede")
# One caption sits in front of two adjacent fields; the nearer is the first
# name.  Fakturama 2.2.0 writes it as "First Name Last Name".
LBL_FIRST_AND_LAST: Tuple[str, ...] = (
    "First Name Last Name", "First Name and Name", "First name and name",
    "Vorname und Name",
)
TAB_ADDRESS: Tuple[str, ...] = ("Addresses", "Address", "Adresse", "Adressen")
TAB_MISCELLANEOUS: Tuple[str, ...] = ("Miscellaneous", "Verschiedenes")
# Fakturama 2.2.0 has no separate Payment tab -- the payment-method combo sits
# on the Debtor's Miscellaneous tab.  Other builds do split it out, so both are
# tried.
TAB_PAYMENT: Tuple[str, ...] = ("Payment", "Zahlung")
LBL_PAYMENT_METHOD: Tuple[str, ...] = ("Payment", "Zahlung", "Zahlungsart")
TAB_MAIN_ADDRESS: Tuple[str, ...] = ("main address", "Main address", "Hauptadresse")
TAB_ADDITIONAL_ADDRESS: Tuple[str, ...] = (
    "additional address #1", "Additional address #1",
)

LBL_STREET: Tuple[str, ...] = ("Street", "Straße", "Strasse")
LBL_ZIP_CITY: Tuple[str, ...] = ("ZIP - City", "ZIP-City", "ZIP", "PLZ - Ort")
LBL_COUNTRY: Tuple[str, ...] = ("Country", "Land")
LBL_EMAIL: Tuple[str, ...] = ("E-Mail", "EMail", "E-mail")
LBL_TELEPHONE: Tuple[str, ...] = ("Telephone", "Telefon")
LBL_ADDRESS_TYPE: Tuple[str, ...] = ("address type", "Address type", "Adresstyp")
LBL_ALIAS: Tuple[str, ...] = ("Alias name", "Alias", "Aliasname")
LBL_NET_OR_GROSS: Tuple[str, ...] = ("Net or Gross", "net or gross", "Netto oder Brutto")

ROLE_INVOICE_ADDRESS: Tuple[str, ...] = ("invoice address", "Invoice address")
ROLE_DELIVERY_ADDRESS: Tuple[str, ...] = ("delivery address", "Delivery address")
SALUTATION_NONE: Tuple[str, ...] = ("---", "")

# --- terms of payment editor ----------------------------------------------

LBL_PAYMENT_NAME: Tuple[str, ...] = ("Name",)
LBL_PAYMENT_ACCOUNT: Tuple[str, ...] = ("Account", "Konto")
LBL_PAYMENT_DESCRIPTION: Tuple[str, ...] = ("Description", "Beschreibung")
# Fakturama 2.2.0 ships this label untranslated, so the raw resource key is
# what UIA reports.  It is listed first precisely because it is what the real
# build shows; the readable captions remain for versions that translate it.
LBL_PAYMENT_CODE: Tuple[str, ...] = (
    "!editorPaymentPaymentcode!", "selecting/setting/automatic",
    "Payment code", "Zahlungsart",
)
LBL_CASH_DISCOUNT: Tuple[str, ...] = ("cash discount", "Cash discount", "Skonto")
LBL_DISCOUNT_DAYS: Tuple[str, ...] = ("Discount Days", "Discount days", "Skontotage")
LBL_NET_DAYS: Tuple[str, ...] = ("Net Days", "Net days", "Nettotage")
BTN_SET_AS_STANDARD: Tuple[str, ...] = ("Set as standard", "Als Standard setzen")

# --- VAT editor ------------------------------------------------------------

LBL_VAT_NAME: Tuple[str, ...] = ("Name",)
LBL_VAT_DESCRIPTION: Tuple[str, ...] = ("Description", "Beschreibung")
LBL_VAT_CODE: Tuple[str, ...] = (
    "VAT code (E-Invoice)", "VAT code", "USt-Code",
)
LBL_VAT_VALUE: Tuple[str, ...] = ("Value", "Wert")
VAT_CODE_STANDARD: Tuple[str, ...] = ("S (Standard rate)", "S (Standard Rate)", "S")

# --- product editor --------------------------------------------------------

LBL_ITEM_NUMBER: Tuple[str, ...] = ("Item Number", "Item number", "Artikelnummer")
LBL_PRODUCT_NAME: Tuple[str, ...] = ("Name",)
LBL_PRODUCT_DESCRIPTION: Tuple[str, ...] = ("Description", "Beschreibung")
LBL_PRICE_GROSS: Tuple[str, ...] = ("Price (gross)", "price (gross)", "Preis (brutto)")
LBL_COST_PRICE_NET: Tuple[str, ...] = (
    "cost price (net)", "Cost price (net)", "Einkaufspreis (netto)",
)
LBL_STOCK: Tuple[str, ...] = ("Stock", "Bestand")

# The caption beside the items table, used to locate its icon column.  The
# column headers below cannot serve as anchors: the items table is a Nebula
# NatTable and publishes no text at all to UIA, headers included.
LBL_ITEMS: Tuple[str, ...] = ("Items", "Positionen", "Artikel")

# --- items grid columns ----------------------------------------------------

COL_POS: Tuple[str, ...] = ("Pos.", "Pos")
COL_QTY: Tuple[str, ...] = ("Qty.", "Qty", "Anzahl")
COL_ITEM_NO: Tuple[str, ...] = ("Item No.", "Item no.", "Artikel-Nr.")
COL_NAME: Tuple[str, ...] = ("Name",)
COL_DESCRIPTION: Tuple[str, ...] = ("Description", "Beschreibung")
COL_VAT: Tuple[str, ...] = ("VAT", "MwSt.")
COL_UPRICE: Tuple[str, ...] = ("U.Price", "UPrice", "E.Preis")
COL_DISCOUNT: Tuple[str, ...] = ("Discount", "Rabatt")
COL_PRICE: Tuple[str, ...] = ("Price", "Preis")

# --- invoice payment area --------------------------------------------------

CHK_PAID: Tuple[str, ...] = ("paid", "Paid", "bezahlt")
LBL_PAY_VALUE: Tuple[str, ...] = ("Value", "Pay Value", "Betrag")
LBL_DUE_DAYS: Tuple[str, ...] = ("Due Days", "Due days", "Fälligkeit")

# --- documents list --------------------------------------------------------

COL_DOCUMENT: Tuple[str, ...] = ("Document", "Dokument")
COL_STATE: Tuple[str, ...] = ("State", "Status")
COL_TOTAL: Tuple[str, ...] = ("Total", "Summe")
COL_CUSTREF: Tuple[str, ...] = ("Cust.Ref.", "CustRef")
STATE_OPEN: Tuple[str, ...] = ("open", "offen")
STATE_PAID: Tuple[str, ...] = ("paid", "bezahlt")


def first(names: Sequence[str]) -> str:
    """The canonical caption for logs and error messages."""
    return names[0] if names else ""
