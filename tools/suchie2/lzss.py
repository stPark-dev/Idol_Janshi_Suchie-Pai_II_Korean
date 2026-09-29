"""LZSS codec used by the game's executable (decoder at 0x0608124C, init at 0x060811F0).

Stream: u32 big-endian = number of compressed bytes that follow, then the payload.
Payload: flag byte read LSB first; bit 1 = one literal byte, bit 0 = two-byte reference
    offset = lo | (hi & 0xF0) << 4,  length = (hi & 0x0F) + 3.
Window: 4096 bytes, first write at 0xFEE. The game clears only [0, 0xFEE); the tail
[0xFEE, 0x1000) keeps whatever the previous decode left there ("stale").
The decoder stops as soon as the compressed byte count reaches zero, including right
after the low byte of a reference (that half reference is dropped).
"""

N = 4096
START = 0xFEE
MIN_MATCH = 3
MAX_MATCH = 18
MAX_DIST = N - 1          # project limit (4095 back); the SH-2 read-before-write order is not relied on
STALE_LEN = N - START


class LzssError(ValueError):
    pass


def decompress(stream: bytes, stale: bytes | None = None) -> bytes:
    """Decode exactly like the game; `stale` models the uncleared window tail."""
    if len(stream) < 4:
        raise LzssError("stream shorter than its 4-byte header")
    count = int.from_bytes(stream[:4], "big")
    end = 4 + count
    if end > len(stream):
        raise LzssError(f"header says {count} bytes but only {len(stream) - 4} follow")
    win = bytearray(N)
    if stale is not None:
        if len(stale) != STALE_LEN:
            raise LzssError("stale tail must be 18 bytes")
        win[START:] = stale
    r = START
    out = bytearray()
    i = 4
    flags = 0
    while True:
        flags >>= 1
        if not flags & 0x100:
            if i >= end:
                break
            flags = stream[i] | 0xFF00
            i += 1
        if i >= end:
            break
        if flags & 1:
            c = stream[i]
            i += 1
            out.append(c)
            win[r] = c
            r = (r + 1) & (N - 1)
            continue
        lo = stream[i]
        i += 1
        if i >= end:
            break
        hi = stream[i]
        i += 1
        off = lo | ((hi & 0xF0) << 4)
        for k in range((hi & 0x0F) + MIN_MATCH):
            c = win[(off + k) & (N - 1)]
            out.append(c)
            win[r] = c
            r = (r + 1) & (N - 1)
    return bytes(out)


def compress(data: bytes, chain_limit: int = 1024) -> bytes:
    """LZSS with an optimal parse over the matches found (hash chains capped at chain_limit;
    cost: literal 9 bits, reference 17 bits). Matches may read
    the zero-cleared window [0, 0xFEE) and bytes already produced, never the stale tail, so
    the output does not depend on it."""
    buf = bytes(START) + data          # linear index == ring index for every readable byte
    n = len(buf)
    head: dict[int, int] = {}
    prev = [-1] * n

    def insert(p: int) -> None:
        if p + 2 < n:
            key = buf[p] | buf[p + 1] << 8 | buf[p + 2] << 16
            prev[p] = head.get(key, -1)
            head[key] = p

    def longest(c: int) -> tuple[int, int]:
        if c + 2 >= n:
            return 0, 0
        key = buf[c] | buf[c + 1] << 8 | buf[c + 2] << 16
        best_len = best_pos = 0
        p = head.get(key, -1)
        tries = 0
        limit = min(MAX_MATCH, n - c)
        while p >= 0 and c - p <= MAX_DIST and tries < chain_limit:
            ln = 0
            while ln < limit and buf[p + ln] == buf[c + ln]:
                ln += 1
            if ln > best_len:
                best_len, best_pos = ln, p
                if ln == limit:
                    break
            p = prev[p]
            tries += 1
        return (best_len, best_pos) if best_len >= MIN_MATCH else (0, 0)

    for p in range(START):
        insert(p)
    # every prefix of the longest match at c is itself a valid match from the same source
    matches = []
    for c in range(START, n):
        matches.append(longest(c))
        insert(c)
    m = n - START
    cost = [0] * (m + 1)
    step = [0] * (m + 1)
    for k in range(m - 1, -1, -1):
        best, how = cost[k + 1] + 9, 0
        ln, _ = matches[k]
        for L in range(MIN_MATCH, ln + 1):
            v = cost[k + L] + 17
            if v < best:
                best, how = v, L
        cost[k], step[k] = best, how
    items: list[tuple[int, int] | int] = []
    k = 0
    while k < m:
        L = step[k]
        if L:
            items.append((matches[k][1] & (N - 1), L))
            k += L
        else:
            items.append(buf[START + k])
            k += 1

    payload = bytearray()
    for g in range(0, len(items), 8):
        group = items[g:g + 8]
        flag = 0
        body = bytearray()
        for b, it in enumerate(group):
            if isinstance(it, int):
                flag |= 1 << b
                body.append(it)
            else:
                off, ln = it
                body += bytes([off & 0xFF, ((off >> 4) & 0xF0) | (ln - MIN_MATCH)])
        payload.append(flag)
        payload += body
    return len(payload).to_bytes(4, "big") + bytes(payload)
