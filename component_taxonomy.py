"""
Rough canonical taxonomies of common design-system terminology, each with
aliases seen in the wild — used to classify crawled page titles (e.g.
"Button - Components - Atlassian Design", "Modal | Carbon Design System")
into a shared vocabulary so the same concept can be browsed across systems.

Three distinct kinds of page, kept as separate taxonomies (see
generate_components.py, which builds one browse-by-X page per taxonomy)
rather than one flat list — they answer genuinely different questions and
mixing them was itself a bug: a "Typography" foundations page was showing up
mixed in with actual UI components, which isn't what either kind of visitor
is looking for.

- COMPONENTS: atomic, reusable UI widgets (Button, Modal, Table) — the
  things you'd drop into a screen.
- PATTERNS: bigger task-/flow-oriented compositions, usually built FROM
  several components (Onboarding, Search, Empty State) — how those widgets
  get assembled to solve a real user task.
- FOUNDATIONS: system-wide design principles that aren't components at all
  (Typography, Color, Iconography, Spacing) — the rules everything else is
  built on top of, not something you'd ever "add to a screen" on its own.

This is inherently approximate — titles are messy and terminology differs
across systems (Modal vs Dialog, Toggle vs Switch, Select vs Dropdown) — so
entries list the common aliases together, and a page can match more than one
canonical name if its title genuinely mentions multiple (rare, harmless).

Matching itself (see generate_components.py's build_component_index) requires
an alias to be the START of the page's cleaned title, not just present
anywhere in it — a title like "Track Your Progress" contains "progress" but
isn't a Progress-component doc page, and matching anywhere in the string is
exactly what let pages like that through before.
"""

COMPONENTS = {
    "Accordion": ["accordion", "expander", "disclosure"],
    "Alert": ["alert", "banner", "notification", "callout", "inline message"],
    "Avatar": ["avatar"],
    "Badge": ["badge"],
    "Breadcrumb": ["breadcrumb", "breadcrumbs"],
    "Button": ["button"],
    "Button Group": ["button group"],
    "Card": ["card"],
    "Carousel": ["carousel", "slideshow"],
    "Checkbox": ["checkbox"],
    "Chip": ["chip", "tag"],
    "Code Snippet": ["code snippet", "code block"],
    "Color Picker": ["color picker", "colour picker"],
    "Combobox": ["combobox", "autocomplete"],
    "Date Picker": ["date picker", "datepicker", "calendar", "date input"],
    "Modal": ["modal", "dialog"],
    "Divider": ["divider", "separator"],
    "Drawer": ["drawer", "side panel", "sidesheet", "bottom sheet"],
    "Dropdown": ["dropdown", "select", "menu button"],
    "File Upload": ["file upload", "upload"],
    "Footer": ["footer"],
    "Icon": ["icon", "icons"],
    "Input": ["input", "text field", "textfield", "text input", "text area", "textarea", "help text", "helper text", "fieldset", "search input", "search box", "search field"],
    "Label": ["label", "form label"],
    "Link": ["link", "hyperlink"],
    "List": ["list"],
    "Menu": ["menu", "context menu"],
    "Navigation Bar": ["navigation bar", "nav bar", "navbar", "top nav", "app bar", "action bar", "header"],
    "Page Header": ["page header", "page title"],
    "Pagination": ["pagination", "pager"],
    "Popover": ["popover"],
    "Progress Bar": ["progress bar", "progress indicator"],
    "Radio": ["radio", "radio button", "radio group"],
    "Rich Text Editor": ["rich text editor", "text editor", "wysiwyg"],
    "Segmented Control": ["segmented control", "segmented buttons"],
    "Sidebar": ["sidebar", "side navigation"],
    "Skeleton": ["skeleton", "placeholder loading"],
    "Slider": ["slider", "range"],
    "Spinner": ["spinner", "loader", "loading indicator"],
    "Stepper": ["stepper", "steps"],
    "Switch": ["switch", "toggle"],
    "Table": ["table", "data table", "dynamic table", "table tree"],
    "Tabs": ["tabs", "tab"],
    "Toast": ["toast", "snackbar"],
    "Tooltip": ["tooltip"],
    "Tree": ["tree", "tree view", "treeview"],
}

# Bigger, task-oriented compositions — usually built from several components
# above, not a single widget. Distinct from COMPONENTS on purpose: someone
# looking for "how do other systems handle onboarding" wants worked examples
# of a whole flow, not a Button doc page.
PATTERNS = {
    "Empty State": ["empty state"],
    "Onboarding": ["onboarding", "walkthrough", "getting started flow"],
    "Search": ["search pattern", "search results", "search experience"],
    "Form Validation": ["form validation", "validation pattern", "error handling"],
    "Data Table Pattern": ["data table pattern", "table interactions", "sorting and filtering"],
    "Notifications": ["notification pattern", "notification center", "in-app messaging"],
    "Wizard": ["wizard", "multi-step form", "multi step form"],
    "Filtering": ["filtering", "filter pattern", "faceted search"],
    "Navigation Pattern": ["navigation pattern", "information architecture", "wayfinding"],
}

# System-wide principles, not components at all — nothing here is something
# you'd "add to a screen" on its own; it's the rules everything else follows.
FOUNDATIONS = {
    "Typography": ["typography", "type scale", "text styles"],
    "Color": ["color", "colour", "color palette", "colour palette"],
    "Iconography": ["iconography", "icon library", "icon set", "icon guidelines"],
    "Spacing": ["spacing", "spacing scale", "layout spacing"],
    "Elevation": ["elevation", "shadows", "z-index"],
    "Motion": ["motion", "animation", "transitions", "motion design"],
    "Grid": ["grid", "layout grid", "responsive grid"],
    "Accessibility": ["accessibility", "a11y guidelines"],
    "Voice and Tone": ["voice and tone", "voice & tone", "content style", "writing style"],
    "Tokens": ["design tokens", "tokens", "token"],
    "Theming": ["theming", "theme", "themes", "dark mode", "light mode"],
}

# Backward-compat alias — earlier code imported TAXONOMY directly for what
# was, at the time, only ever components.
TAXONOMY = COMPONENTS

# A last-resort override for a canonical whose alias legitimately matches a
# page that plainly isn't documentation of it — after whole-word matching
# and scope-filtering (a page must be on the system's own crawled domain
# under its start_url's path), the one case neither catches is a system
# whose start_url IS the bare domain root: a marketing/community page there
# is just as "in scope" as its real docs, since there's no narrower path to
# exclude it by. Confirmed live: Designers Italia's start_url is its site
# root, and "Accessibility Days 2025" (a conference announcement under
# /community/eventi/) legitimately starts with the "accessibility" alias.
# Keyed by canonical name; each value is matched against the RAW page title
# (not the cleaned one) with re.search, case-insensitive.
NEGATIVE_TITLE_PATTERNS: dict[str, list[str]] = {
    "Accessibility": [r"\bdays?\b.*20\d\d", r"\bconference\b", r"\bevent\b"],
    "Onboarding": [r"for businesses", r"^onboarding resources"],
}
