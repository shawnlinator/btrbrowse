def human_size(n):
    """1536 -> "1.5 KB", 11 -> "11 bytes" (binary units, like most file managers show)."""
    n = float(n)
    if n < 1024:
        return "%d byte%s" % (n, "" if n == 1 else "s")
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
