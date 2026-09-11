"""
A rough canonical taxonomy of common design-system component/pattern names,
each with aliases seen in the wild. Used to classify crawled page titles
(e.g. "Button - Components - Atlassian Design", "Modal | Carbon Design System")
into a shared vocabulary so the same component can be browsed across systems.

This is inherently approximate — titles are messy and terminology differs
across systems (Modal vs Dialog, Toggle vs Switch, Select vs Dropdown) — so
entries list the common aliases together, and a page can match more than one
canonical name if its title genuinely mentions multiple (rare, harmless).
"""

TAXONOMY = {
    "Accordion": ["accordion", "expander", "disclosure"],
    "Alert": ["alert", "banner", "notification", "callout"],
    "Avatar": ["avatar"],
    "Badge": ["badge"],
    "Breadcrumb": ["breadcrumb", "breadcrumbs"],
    "Button": ["button"],
    "Card": ["card"],
    "Carousel": ["carousel", "slideshow"],
    "Checkbox": ["checkbox"],
    "Chip": ["chip", "tag"],
    "Combobox": ["combobox", "autocomplete"],
    "Date Picker": ["date picker", "datepicker", "calendar"],
    "Modal": ["modal", "dialog"],
    "Divider": ["divider", "separator"],
    "Drawer": ["drawer", "side panel", "sidesheet"],
    "Dropdown": ["dropdown", "select", "menu button"],
    "Empty State": ["empty state"],
    "Icon": ["icon", "icons"],
    "Input": ["input", "text field", "textfield"],
    "Link": ["link", "hyperlink"],
    "List": ["list"],
    "Menu": ["menu", "navigation menu"],
    "Navigation": ["navigation", "nav bar", "navbar"],
    "Pagination": ["pagination", "pager"],
    "Popover": ["popover"],
    "Progress": ["progress", "progress bar", "progress indicator"],
    "Radio": ["radio", "radio button", "radio group"],
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
    "Typography": ["typography", "heading", "text styles"],
}
