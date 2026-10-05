/* Voice subtitles (SUB.BIN) for Idol Janshi Suchie-Pai II, Saturn, disc 1.

   SUB.BIN = this code (up to 0x1C00 bytes) + the index of groups and trigger files appended by
   the build at BASE+0x1C00.  It is read once by the stub in the executable on the first
   file load, then installs its hooks in RAM:
     - every literal holding the voice player 0x06055C80 / its wrapper 0x06055E88
       -> BASE+0x18 / BASE+0x1C (sub_on_play, then the original);
     - the vblank callback's call of 0x0608C9E4 (library frame end, VDP2 register upload)
       -> BASE+0x14 (the original, then sub_on_vblank).

   Subtitles come in groups (a scene's voices, their cues and the glyphs they use, packed
   with the game's LZSS into SUBDAT.BIN).  A group is read when the game loads one of the
   scene's files (the index lists those trigger files), the way the game's file loader
   reads; reading while a scene runs stalls it (a VDP1 frame-end interrupt is missed and
   the frame wait never returns).  A voice that starts is looked up by name in the loaded
   group.  Cue times count real vblanks from the moment the voice is heard: the PCM library
   streams voices through an SCSP slot playing sound RAM 0x7C000, and that slot keys on
   after the CD has delivered the first block.

   Text goes on VDP2 NBG3, which the scenes leave unused.  The scenes with voices are
   NBG0/NBG1 bitmaps plus VDP1 sprites, laid out differently from scene to scene, so the
   VRAM block, access slots and palette row are chosen at run time from the library's RAM
   copies of the registers (choose_place); a scene that does not qualify gets no
   subtitles.  VDP2 registers are write-only and re-uploaded from those copies, so our NBG3
   fields are written right after the library's upload, and restored from the copies
   afterwards.

   Built with sh4-linux-gnu-gcc -m4-nofpu -mb -Os; check_sh2.py rejects any opcode the SH-2
   does not have.  No libc, no division, no variable shifts. */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef short s16;

#define BASE        0x060F6800u
#define INDEX       ((const u8 *)0x060F8400)    /* BASE + 0x1C00, appended by the build */
#define DATA        ((const u8 *)0x060F9800)    /* group data, up to GROUP_MAX bytes */
#define GROUP_MAX   0x5000
#define CELLBUF     ((u32 *)0x060FEC00)         /* 160 NBG3 cells, up to 0x06100000 */
#define INDEX_MAGIC 0x4B484958u                 /* "KHIX" */
#define GROUP_MAGIC 0x4B484732u                 /* "KHG2" */

#define VRAM_BASE   0x25E00000u
#define CRAM16      ((volatile u16 *)0x25F00000)
#define VDP2_REG(o) (*(volatile u16 *)(0x25F80000 + (o)))
#define SCSP_SLOT(i, o) (*(volatile u16 *)(0x25B00000 + (i) * 0x20 + (o)))

#define VBLANKS     (*(volatile u32 *)0x060BE720)   /* +1 in the vblank callback */
#define VOICE_SA    0x7C000                         /* PCM library stream buffer */
#define KEYON_WAIT      180
#define KEYON_FALLBACK  30

/* ---- small helpers: gcc for SH-4 turns constant shifts into shad, force SH-2 sequences
   (shll/shlr set T: the "t" clobber keeps gcc from testing a stale T across them) */
static inline u32 shr1(u32 x) { __asm__("shlr %0" : "+r"(x) : : "t"); return x; }
static inline u32 shr2(u32 x) { __asm__("shlr2 %0" : "+r"(x) : : "t"); return x; }
static inline u32 shr3(u32 x) { __asm__("shlr2 %0\n\tshlr %0" : "+r"(x) : : "t"); return x; }
static inline u32 shr4(u32 x) { __asm__("shlr2 %0\n\tshlr2 %0" : "+r"(x) : : "t"); return x; }
static inline u32 shr6(u32 x) { __asm__("shlr2 %0\n\tshlr2 %0\n\tshlr2 %0" : "+r"(x) : : "t"); return x; }
static inline u32 shl1(u32 x) { __asm__("shll %0" : "+r"(x) : : "t"); return x; }
static inline u32 shl2(u32 x) { __asm__("shll2 %0" : "+r"(x) : : "t"); return x; }
static inline u32 shl4(u32 x) { __asm__("shll2 %0\n\tshll2 %0" : "+r"(x) : : "t"); return x; }
static inline u32 shl5(u32 x) { __asm__("shll2 %0\n\tshll2 %0\n\tshll %0" : "+r"(x) : : "t"); return x; }
static inline u32 shl6(u32 x) { __asm__("shll2 %0\n\tshll2 %0\n\tshll2 %0" : "+r"(x) : : "t"); return x; }

static inline void purge_cache(void)
{
    volatile u8 *ccr = (volatile u8 *)0xFFFFFE92;
    *ccr = *ccr | 0x10;
}

static int upper(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }

/* ---- diagnostic log (LOG builds): ring of 16-byte records in the cell buffer area ------ */
#ifdef LOG
struct logrec { u32 vbl; u8 kind, b1, b2, b3; char name[8]; };
#define LOGBUF  ((volatile struct logrec *)0x060FEC00)
#define LOGN    256                                     /* 4 KB */
static u32 log_pos = 0;
static void logit(int kind, const char *name, int b1, int b2)
{
    volatile struct logrec *r = LOGBUF + (log_pos & (LOGN - 1));
    r->vbl = VBLANKS;
    r->kind = (u8)kind;
    r->b1 = (u8)b1;
    r->b2 = (u8)b2;
    r->b3 = 0;
    for (int i = 0; i < 8; i++)
        r->name[i] = name && name[0] && (i == 0 || name[i - 1]) ? name[i] : 0;
    log_pos++;
    *(volatile u32 *)0x060FEBFC = log_pos;
}
#else
#define logit(k, n, a, b) ((void)(n))
#endif

/* ---- runtime hooks ---------------------------------------------------------------------- */
static const u32 play_lits[] = {
    0x06005D90, 0x06005E38, 0x06005FF0, 0x06006170, 0x06006318, 0x0600915C, 0x0601CDAC, 0x0601D21C,
    0x0601DD38, 0x0601DE00, 0x0601E130, 0x0601E86C, 0x0601F098, 0x0601F518, 0x0601F5DC, 0x06020B10,
    0x06021EA8, 0x06022458, 0x06023DF0, 0x060271DC, 0x0602AA20, 0x0602AD7C, 0x0602FAF8, 0x06032430,
    0x06032524, 0x06032630, 0x060347CC, 0x06034FCC, 0x06035D14, 0x06036564, 0x06037450, 0x06037ED8,
    0x06038BA8, 0x06039800, 0x0603A47C, 0x0603B7AC, 0x0603CEDC, 0x0603EA38, 0x0603EED0, 0x06041C1C,
    0x06044848, 0x060448D0, 0x06044B5C, 0x0604E398, 0x0604E90C, 0x0604EA50, 0x0604EB40, 0x0604FAD4,
    0x0604FEC0, 0x06051320, 0x06051808, 0x06051904, 0x060519EC, 0x06054AFC, 0x0606A66C, 0x0606AABC,
    0x0606B074, 0x0606CC78, 0x0606D16C, 0x0606D630, 0x0606DB78, 0x0606EBE0, 0x060702F4, 0x0607EF20,
    0x0607F104, 0x0608493C,
};
static const u32 play2_lits[] = {
    0x0602AFC8, 0x0602AFF8, 0x06071304, 0x060729B8, 0x06074AE8, 0x06076700, 0x060767C8, 0x060773D8,
    0x0608144C,
};

static int hooked = 0;

static void patch(const u32 *lits, unsigned n, u32 orig, u32 hook)
{
    for (unsigned i = 0; i < n; i++) {
        volatile u32 *lit = (volatile u32 *)lits[i];
        if (*lit == orig)
            *lit = hook;
    }
}

static void install_hooks(void)
{
    if (hooked)
        return;
    hooked = 1;
    patch(play_lits, sizeof play_lits / 4, 0x06055C80, BASE + 0x18);
    patch(play2_lits, sizeof play2_lits / 4, 0x06055E88, BASE + 0x1C);
    if (*(volatile u32 *)0x060046F8 == 0x0608C9E4)
        *(volatile u32 *)0x060046F8 = BASE + 0x14;
    purge_cache();
}

/* ---- index and group data ------------------------------------------------------------ */
/* index (BASE+0x1C00): header, ngroups x {byte offset in SUBDAT.BIN, packed and unpacked sizes}, then the
   trigger files: a group is read when the game loads one of its scene files (the CD is
   busy loading anyway), never while a scene runs.  A file may trigger several groups (a
   scene file shared by variants); then the loaded group is kept if it lists the file. */
struct index_header { u32 magic; u16 ntriggers, ngroups; };
struct index_group { u32 offset; u16 packed, size; };   /* LZSS stream in SUBDAT.BIN */
struct trigger { char name[12]; u8 group, pad[3]; };

/* group: header, voice names (8 bytes, without ".AIF"), cues, glyph table, text, bits */
struct group_header {
    u32 magic;
    u16 group, nvoices, ncues, nglyphs;
    u32 names, cues, glyphs, text, bits;
};
struct cue { u8 voice, nlines; u16 start, end, text, n1, n2; s16 x1, x2; };
struct glyph { u16 width, bits; };

/* what the file loader 0x06006874 does, with a sector offset: stop the voice stream, look the
   name up, GFS_Load(fid, sector, buffer, bytes) */
#define CD_READY    (*(volatile u8 *)0x060C65A0)
#define VOICE_STOP  ((void (*)(void))0x06055C02)
#define GFS_NAME2ID ((int (*)(const char *))0x0608FF20)
#define GFS_LOAD    ((int (*)(int, int, void *, int))0x060902C2)

static int loaded_group = -1;

static u32 shr_n(u32 x, int n) { while (n-- > 0) x = shr1(x); return x; }
static u32 shl_n(u32 x, int n) { while (n-- > 0) x = shl1(x); return x; }

/* The game's LZSS format (tools/suchie2/lzss.py) without its 4-byte header: flag byte read
   LSB first, 1 = literal, 0 = reference lo, hi: window position lo | (hi & 0xF0) << 4,
   length (hi & 0x0F) + 3.  The 4 KB window starts writing at 0xFEE over zeros, so window
   position p is the latest output byte k' with (0xFEE + k') & 0xFFF == p, or 0 before
   the output began; the output itself serves as the window. */
static void unlzss(const u8 *in, u32 n, u8 *out)
{
    const u8 *end = in + n;
    u32 flags = 0;
    int k = 0;
    while (in < end) {
        flags = shr1(flags);
        if (!(flags & 0x100)) {
            flags = *in++ | 0xFF00;
            if (in >= end)
                break;
        }
        if (flags & 1) {
            out[k++] = *in++;
            continue;
        }
        if (in + 1 >= end)
            break;                          /* a half reference at the end is dropped */
        int lo = *in++, hi = *in++;
        int p = lo | (int)shl4(hi & 0xF0);
        int len = (hi & 0x0F) + 3;
        int back = (0xFEE + k - p) & 0xFFF;
        if (!back)
            back = 0x1000;
        for (int j = 0; j < len; j++, k++)
            out[k] = k - back >= 0 ? out[k - back] : 0;
    }
}


static int load_group(int g)
{
    const struct index_header *ih = (const struct index_header *)INDEX;
    const struct index_group *gr = (const struct index_group *)(INDEX + 8) + g;
    if (loaded_group == g)
        return 1;
    loaded_group = -1;
    if (g >= ih->ngroups)
        return 0;
    /* the sectors holding the packed group go to the end of the data area; the group is
       unpacked from there to its start (the build checks the output never overtakes the input) */
    u32 first = shr_n(gr->offset, 11), last = shr_n(gr->offset + gr->packed - 1, 11);
    u32 bytes = shl_n(last - first + 1, 11);
    u8 *buf = (u8 *)DATA + GROUP_MAX - bytes;
    CD_READY = 0;
    VOICE_STOP();
    int fid = GFS_NAME2ID("SUBDAT.BIN");
    int n = fid >= 0 ? GFS_LOAD(fid, (int)first, buf, (int)bytes) : -1;
    CD_READY = 1;
    if (n > 0) {
        purge_cache();
        unlzss(buf + (gr->offset & 2047), gr->packed, (u8 *)DATA);
        const struct group_header *h = (const struct group_header *)DATA;
        if (h->magic == GROUP_MAGIC && h->group == g)
            loaded_group = g;
    }
    logit(4, "SUBDAT", loaded_group == g, g);
    return loaded_group == g;
}
/* file names compare case-insensitively; n = bytes of the stored name */
static int same_name(const char *game, const char *stored, int n, int stop_at_dot)
{
    int k = 0;
    while (k < n && stored[k] && upper(game[k]) == stored[k])
        k++;
    if (k < n && stored[k])
        return 0;
    return game[k] == 0 || (stop_at_dot && game[k] == '.');
}

static void on_trigger(const char *name)
{
    const struct index_header *h = (const struct index_header *)INDEX;
    if (h->magic != INDEX_MAGIC)
        return;
    const struct trigger *t = (const struct trigger *)(INDEX + 8 + shl2(shl1(h->ngroups)));
    int first = -1;
    for (int i = 0; i < h->ntriggers; i++, t++) {
        if (!same_name(name, t->name, 12, 0))
            continue;
        if (t->group == loaded_group)
            return;                 /* the loaded group already covers this scene file */
        if (first < 0)
            first = t->group;
    }
    if (first >= 0)
        load_group(first);
}

/* the voice's number in the loaded group, or -1 */
static int find_voice(const char *name)
{
    if (loaded_group < 0)
        return -1;
    const struct group_header *h = (const struct group_header *)DATA;
    const char *n = (const char *)(DATA + h->names);
    for (int i = 0; i < h->nvoices; i++, n += 8)
        if (same_name(name, n, 8, 1))
            return i;
    return -1;
}

/* ---- voice timing ----------------------------------------------------------------------- */
static int cur_voice = -1;          /* voice number within the loaded group, -1 = none */
static u32 called = 0, anchor = 0;
static u8 started = 0, seen_off = 0;

static int voice_slot_on(void)
{
    for (int i = 0; i < 32; i++) {
        u16 r0 = SCSP_SLOT(i, 0);
        if ((r0 & 0x0800) && ((u32)(r0 & 0x000F) << 16 | SCSP_SLOT(i, 2)) == VOICE_SA)
            return 1;
    }
    return 0;
}

static void watch_keyon(u32 now)
{
    if (started || cur_voice < 0)
        return;
    if (!voice_slot_on()) {
        seen_off = 1;
    } else if (seen_off) {
        anchor = now;
        started = 1;
        logit(3, 0, cur_voice, (u8)(now - called));
        return;
    }
    if (now - called >= KEYON_WAIT) {
        anchor = called + KEYON_FALLBACK;
        started = 1;
        logit(3, "fallback", cur_voice, 0);
    }
}

void sub_on_load(const char *name)
{
    install_hooks();
    logit(1, name, 0, 0);
    on_trigger(name);
}

void sub_on_play(const char *name)
{
    install_hooks();
    cur_voice = find_voice(name);
    logit(2, name, loaded_group, cur_voice);
#ifdef LOG
    cur_voice = 0;                  /* time every voice's key-on */
#endif
    called = VBLANKS;
    started = seen_off = 0;
}

/* ---- NBG3 text layer -------------------------------------------------------------------- */
/* NBG3 uses 2x2-cell characters (16x16, 128 bytes) and is shown only in the band of the two
   text lines (a VDP2 window makes it transparent elsewhere), so the map needs nothing but
   the two rows on screen (20 entries each) and every character can sit anywhere in its
   128 KB quarter of VRAM.  Each character holds a 16x16 piece of a text line. */
#define N3_COLS     40              /* 8-pixel cells per line in the cell buffer */
#define N3_CELLS    (4 * N3_COLS)   /* two text lines, two cell rows each */
#define N3_CHARS    40              /* 2 lines x 20 characters of 16x16 */
#define BAND_Y      200             /* first screen line of the text band (two lines: 200..231) */
#define GLYPH_ROWS  16

/* library RAM copies of the VDP2 registers (verified against the chip in save states) */
#define SHADOW(reg)  (*(volatile u16 *)(0x060B4E30 + (reg)))   /* registers below 0xE0 */

/* SCYIN3: writing it reloads NBG3's line counter, so a write after the display has started
   (our hook runs late in some scenes) shifts the text down by the lines already drawn.  It
   is written only when it changes, and also put in the library's copy so that a full
   register upload in vblank keeps it (the game leaves NBG3 alone). */
static u16 set_scy3 = 0;                            /* bit 15: we wrote it */

static u16 shadow(int reg)
{
    if (reg < 0xE0)
        return *(volatile u16 *)(0x060B4E30 + reg);
    return *(volatile u16 *)(0x060B5D2C + reg - 0xE0 - (reg > 0xFE ? 2 : 0));
}

/* Where NBG3 goes in the current scene, chosen from the library's register copies.
   Only scenes made of NBG0/NBG1 bitmaps (and VDP1 sprites) qualify: cell layers keep their
   character data anywhere in VRAM, so nothing can be shown to be free there.
   The map rows and the characters take 41 separate 128-byte slots of VRAM that are not
   on screen (find_slots), so even scenes whose bitmaps fill VRAM leave room: rows below
   the screen, the hidden right part of a 512-pixel-wide bitmap. */
struct place {
    u32 map;                        /* address of the first of the two map rows */
    u32 chars[N3_CHARS + 1];        /* address of each character (+ a blank one in page mode) */
    u16 page_row;                   /* map row of the first text line within its 2 KB page */
    u16 cyc_reg, cyc_lo, cyc_hi;    /* access pattern pair holding our two slots */
    u16 pal, caos;                  /* palette number and colour RAM offset */
    u16 win;                        /* 0: window 0, 1: window 1, 2: none, a whole page of our own */
    u16 sig[11];                    /* register copies the choice was made from */
};
static struct place place;
static int placed = 0;              /* place is valid for place.sig */
static int relayouts = 0;
static int n3_cur = -2;             /* cue in the chars, -1 = blank, -2 = unknown */

/* BGON CHCTLA CHCTLB MPOFN RAMCTL WCTLA WCTLB and the bank A/B access patterns: a scene may
   change its windows or layers after it starts, and the choice must follow */
static const u16 sig_regs[11] = { 0x20, 0x28, 0x2A, 0x3C, 0x0E, 0xD0, 0xD2, 0x10, 0x12, 0x18, 0x1A };

static int sig_same(void)
{
    for (int i = 0; i < 11; i++)
        if (place.sig[i] != shadow(sig_regs[i]))
            return 0;
    return 1;
}

struct bitmap { u32 base, size; int sshift, hrows, sy, bpp_shift; };

/* bitmap layer n (0 or 1), or 0 if it is off or a cell layer */
static int bitmap_of(int n, struct bitmap *b)
{
    u32 ctl = shadow(0x28);
    if (n)
        ctl = shr_n(ctl, 8);
    if (!(shadow(0x20) & (n ? 2 : 1)) || !(ctl & 0x02))
        return 0;
    int sz = shr2(ctl) & 3, cc = shr4(ctl) & 7;
    b->bpp_shift = cc == 0 ? -1 : cc == 1 ? 0 : cc <= 3 ? 1 : 2;    /* log2 bytes per pixel */
    b->sshift = ((sz & 2) ? 10 : 9) + b->bpp_shift;                /* log2 stride */
    b->hrows = (sz & 1) ? 512 : 256;
    b->base = shl_n((n ? shr4(shadow(0x3C)) : shadow(0x3C)) & 7, 17);
    b->size = shl_n((u32)b->hrows, b->sshift);
    b->sy = shadow(n ? 0x84 : 0x74) & (b->hrows - 1);
    return 1;
}

static int zero(u32 addr, int bytes)
{
    volatile u32 *v = (volatile u32 *)(VRAM_BASE + addr);
    for (int i = 0; i < (int)shr2(bytes); i++)
        if (v[i])
            return 0;
    return 1;
}

/* two adjacent unused slots T(k), T(k+1) in the pattern pair at reg (T0..T3, then T4..T7) */
static int pick_slots(u32 addr)
{
    int bank_b = addr >= 0x40000;
    int second = (addr & 0x20000) && (shadow(0x0E) & (bank_b ? 0x0200 : 0x0100));
    u16 reg = (u16)((bank_b ? 0x18 : 0x10) + (second ? 4 : 0));
    u16 bg = shadow(0x20), ctl = shadow(0x28);
    u32 pat = shl_n(shadow(reg), 16) | shadow(reg + 2);
    for (int k = 0; k < 7; k++) {
        int ok = 1;
        for (int j = k; j <= k + 1; j++) {
            int c = shr_n(pat, 28 - shl2(j)) & 0xF;
            /* free: no access, a layer that is off, or the name reads of a bitmap layer;
               NBG3's own reads (3, 7) are left from an earlier frame of ours */
            int unused = c == 0xF || c == 3 || c == 7
                || (c == 0 && (!(bg & 1) || (ctl & 0x0002)))
                || (c == 1 && (!(bg & 2) || (ctl & 0x0200)))
                || (c == 2 && !(bg & 4))
                || (c == 4 && !(bg & 1)) || (c == 5 && !(bg & 2)) || (c == 6 && !(bg & 4));
            if (!unused)
                ok = 0;
        }
        if (ok) {
            int sh = 24 - shl2(k);
            pat = (pat & ~shl_n(0xFF, sh)) | shl_n(0x37, sh);     /* N3 name, then N3 character */
            place.cyc_reg = reg;
            place.cyc_lo = (u16)shr_n(pat, 16);
            place.cyc_hi = (u16)pat;
            return 1;
        }
    }
    return 0;
}

static const u16 col[4] = { 0, 0x8000, 0x8000 | 0x5294, 0xFFFF };   /* clear, black, grey, white */

static int pick_cram(void)
{
    if ((shr4(shr4(shadow(0x0E))) & 3) > 1)       /* RAMCTL.CRMD: 32-bit colour RAM not handled */
        return 0;
    static const u8 rows[] = { 13, 12, 14, 15, 4, 5, 6, 7 };
    for (unsigned i = 0; i < sizeof rows; i++) {
        volatile u16 *c = CRAM16 + shl4(rows[i]);
        int ok = 1;
        for (int j = 1; j < 16; j++)
            if (c[j] && !(j < 4 && c[j] == col[j]))
                ok = 0;
        if (ok) {
            place.pal = rows[i] & 15;
            place.caos = shr4(rows[i]);
            return 1;
        }
    }
    return 0;
}

/* a window no layer uses (window enable bits 1 and 3 of each byte of WCTLA..WCTLD) */
static int pick_window(void)
{
    u16 used = shadow(0xD0) | shadow(0xD2) | shadow(0xD4) | shadow(0xD6);
    if (!(used & 0x0202)) {
        place.win = 0;
        return 1;
    }
    if (!(used & 0x0808)) {
        place.win = 1;
        return 1;
    }
    return 0;
}

/* VRAM the scene's cell layers use: their map planes and every character their maps name,
   as a bit per 128-byte slot (4096 slots, kept in the free RAM after the group data). */
#define CELLUSE     ((u8 *)0x060FE800)

static const u8 bit8[8] = { 1, 2, 4, 8, 16, 32, 64, 128 };

static void mark(u32 a, u32 bytes)
{
    a &= 0x7FFFF;
    for (u32 s = shr3(shr4(a)), e = shr3(shr4(a + bytes - 1)); s <= e && s < 4096; s++)
        CELLUSE[shr3(s)] |= bit8[s & 7];
}

static int marked(u32 a)
{
    u32 s = shr3(shr4(a));
    return CELLUSE[shr3(s)] & bit8[s & 7];
}

/* cell layer n (0..2; NBG3 is ours): mark its maps and characters */
static void mark_cell_layer(int n)
{
    static const u8 chctl_shift[3] = { 0, 8, 0 };
    u16 ctl = shr_n(shadow(n < 2 ? 0x28 : 0x2A), chctl_shift[n]);
    int big = n < 2 ? (ctl & 1) : (ctl & 1);                    /* 2x2 characters */
    int cc = n < 2 ? shr4(ctl) & 7 : shr1(ctl) & 1;             /* colour count code */
    u32 cell = cc == 0 ? 32 : cc == 1 ? 64 : cc == 2 ? 128 : cc == 3 ? 256 : 512;
    u32 chr = big ? shl2(cell) : cell;
    u16 pncn = shadow(0x30 + shl1(n));
    int one = pncn & 0x8000, aux = pncn & 0x4000, scn = pncn & 0x1F;
    u32 page = shl_n(big ? 0x800 : 0x2000, one ? 0 : 1);
    int plsz = shr_n(shadow(0x3A), shl1(n)) & 3;
    u32 pages = plsz == 0 ? 1 : plsz == 1 ? 2 : 4;
    int mpof = shr_n(shadow(0x3C), shl2(n)) & 7;
    /* character number = hi | (pattern name field << sh) | lo */
    u32 hi, lo, field;
    int sh;
    if (!aux) {
        field = 0x3FF;
        hi = big ? shl_n(scn & 0x1C, 10) : shl_n(scn, 10);
        lo = big ? (u32)(scn & 3) : 0;
    } else {
        field = 0xFFF;
        hi = big ? shl_n(scn & 0x10, 10) : shl_n(scn & 0x1C, 10);
        lo = big ? (u32)(scn & 3) : 0;
    }
    sh = big ? 2 : 0;
    u32 done[4];
    for (int p = 0; p < 4; p++) {
        u16 ab = shadow(0x40 + shl2(n) + (p >= 2 ? 2 : 0));
        int mp = (p & 1 ? shr4(shr4(ab)) : ab) & 0x3F;
        mp &= plsz == 0 ? 0x3F : plsz == 1 ? 0x3E : 0x3C;
        u32 base = ((u32)(shl_n(mpof, 6) | mp) * page) & 0x7FFFF;
        done[p] = base;
        int dup = 0;
        for (int q = 0; q < p; q++)
            dup |= done[q] == base;
        if (dup)
            continue;
        mark(base, page * pages);
        volatile u16 *m = (volatile u16 *)(VRAM_BASE + base);
        u32 n_ent = shr1(page * pages);
        if (one) {
            for (u32 e = 0; e < n_ent && base + shl1(e) < 0x80000; e++) {
                u32 f = m[e] & field;
                mark(shl_n(hi | (sh ? shl2(f) : f) | lo, 5), chr);
            }
        } else {
            for (u32 e = 1; e < n_ent && base + shl1(e) < 0x80000; e += 2)
                mark(shl4(shl1(m[e] & 0x7FFF)), chr);
        }
    }
}

/* What a 128-byte slot of VRAM is to the scene: outside every layer (usable when zero, or
   whatever an earlier scene left there when no cell layer is on), inside a bitmap but never
   on screen (usable when zero), or in use.  A bitmap shows the rows from its vertical
   scroll on, and its left 320 pixels unless it scrolls sideways or zooms. */
#define SLOT_OUTSIDE 0
#define SLOT_HIDDEN  1
#define SLOT_SHOWN   2

static int cells_on = 0;            /* a cell layer is on (set by choose_place) */

static int slot_kind(u32 a)
{
    int kind = SLOT_OUTSIDE;
    if (cells_on && marked(a))
        return SLOT_SHOWN;
    for (int n = 0; n < 2; n++) {
        struct bitmap b;
        if (!bitmap_of(n, &b) || a < b.base || a >= b.base + b.size)
            continue;
        kind = SLOT_HIDDEN;
        u32 off = a - b.base, stride = shl_n(1, b.sshift);
        int row = (int)shr_n(off, b.sshift);
        int still = !shadow(n ? 0x80 : 0x70) && !shadow(n ? 0x82 : 0x72)
                    && shadow(n ? 0x88 : 0x78) == 1 && !shadow(n ? 0x8A : 0x7A);
        int col_shown = !still || (off & (stride - 1)) < shl_n(320, b.bpp_shift);
        if (((row - b.sy) & (b.hrows - 1)) < 240 && col_shown)
            return SLOT_SHOWN;
    }
    return kind;
}

static int slot_ok(u32 a)
{
    u32 bk = shl1(shl_n(shadow(0xAC) & 7, 16) | shadow(0xAE));     /* back screen colour word */
    if (bk >= a && bk < a + 128)
        return 0;
    int k = slot_kind(a);
    if (k == SLOT_SHOWN)
        return 0;
    return (k == SLOT_OUTSIDE && !cells_on) || zero(a, 128);
}

/* In the 128 KB quarter [lo, lo + 0x20000): with a window, the two map rows (128 bytes
   inside one 2 KB page) and the 40 characters; without one, a whole 2 KB page (its other
   entries name a blank character, our 41st) and the characters.  Keeping everything in one
   quarter keeps the characters' numbers within one setting of PNCN3's supplementary bits. */
static int find_slots(u32 lo)
{
    int whole = place.win > 1, need = N3_CHARS + 1 + whole;
    for (int a = (int)lo + 0x20000 - 128; a >= (int)lo && need; a -= 128) {
        if (need == N3_CHARS + 1 + whole) {         /* the map */
            if (whole) {
                if (a & 0x780)
                    continue;
                int ok = 1;
                for (int k = 0; k < 16 && ok; k++)
                    ok = slot_ok((u32)a + shl_n(k, 7));
                if (!ok)
                    continue;
                place.map = (u32)a + 0x600;         /* rows 24 and 25 of the page */
                place.page_row = 24;
            } else {
                if (!slot_ok((u32)a) || (a & 0x7FF) > 0x780)
                    continue;
                place.map = (u32)a;
                place.page_row = (u16)shr_n(a & 0x7FF, 6);
            }
            need--;
            continue;
        }
        if (whole && (u32)a >= place.map - 0x600 && (u32)a < place.map + 0x200)
            continue;                               /* inside our own page */
        if (!slot_ok((u32)a))
            continue;
        place.chars[N3_CHARS + whole - need] = (u32)a;
        need--;
    }
    return !need;
}

static int choose_place(void)
{
    u16 bg = shadow(0x20);
    placed = 0;
    for (int i = 0; i < 11; i++)
        place.sig[i] = shadow(sig_regs[i]);
    if (bg & 0x38)                                  /* NBG3 or rotation layers in use */
        return 0;
    if (shadow(0x9A) || shadow(0xE8))               /* line/vertical cell scroll, line colour tables */
        return 0;
    for (int i = 0; i < 512; i++)
        CELLUSE[i] = 0;
    cells_on = 0;
    for (int n = 0; n < 3; n++) {
        struct bitmap b;
        if ((bg & shl_n(1, n)) && (n == 2 || !bitmap_of(n, &b))) {
            mark_cell_layer(n);
            cells_on = 1;
        }
    }
    if (!pick_window())
        place.win = 2;                              /* no free window: a whole page of our own */
    if (!pick_cram())
        return 0;
    for (int q = 3; q >= 0; q--) {                  /* B1, B0, A1, A0 */
        u32 lo = shl_n(q, 17);
        if (pick_slots(lo) && find_slots(lo))
            return placed = 1;
    }
    return 0;
}

/* pattern name of character i: 2x2 characters take the character number / 4 in bits 9..0 */
static u16 n3_pn(int i)
{
    return (u16)(shl4(shl4(shl4(place.pal))) | (shr_n(place.chars[i], 7) & 0x3FF));
}

static void n3_regs(void)
{
    u32 cn = shr_n(place.chars[0], 5);              /* character number = address / 32 */
    u32 page = place.map - shl_n(place.page_row, 6);
    u32 mp = shr_n(page, 11);                       /* 2 KB pages */
    VDP2_REG(0x20) = shadow(0x20) | 0x0008;                     /* BGON: N3ON */
    VDP2_REG(0x2A) = (shadow(0x2A) & ~0x0030) | 0x0010;         /* CHCTLB: N3 2x2 cells, 16 colours */
    VDP2_REG(0x36) = (u16)(0x8000 | shl2(shr_n(cn, 12) & 7));   /* PNCN3: 1 word, char bits 14..12 */
    VDP2_REG(0x3A) = shadow(0x3A) & ~0x00C0;                    /* PLSZ: N3 1x1 plane */
    VDP2_REG(0x3C) = (u16)((shadow(0x3C) & ~0x7000) | shl4(shl4(shl4(shr_n(mp, 6) & 7))));  /* MPOFN */
    VDP2_REG(0x4C) = (u16)((mp & 0x3F) * 0x0101);               /* MPABN3 */
    VDP2_REG(0x4E) = (u16)((mp & 0x3F) * 0x0101);               /* MPCDN3 */
    VDP2_REG(0x94) = 0;                                         /* SCXIN3 */
    /* SCYIN3 reloads NBG3's line counter when written, so a write after the display has
       started (vblank work can run late) shifts the text down; the library writes its RAM
       copy during vblank, so the value goes there too (NBG3 is unused by the game) */
    u16 scy = (u16)((shl4(place.page_row) - BAND_Y) & 0x7FF);  /* SCYIN3: map row at y 200 */
    if (set_scy3 != (scy | 0x8000) || SHADOW(0x96) != scy) {
        SHADOW(0x96) = scy;
        VDP2_REG(0x96) = scy;
        set_scy3 = scy | 0x8000;
    }
    /* window over the band; NBG3 transparent outside it (WnE + WnA = outside) */
    if (place.win < 2) {
        u16 w = (u16)(place.win ? 0xC8 : 0xC0);
        VDP2_REG(w) = 0;                                        /* WPSX: x * 2 in normal resolution */
        VDP2_REG(w + 2) = BAND_Y;
        VDP2_REG(w + 4) = 2 * 319;
        VDP2_REG(w + 6) = BAND_Y + 31;
        VDP2_REG(0xD2) = (u16)((shadow(0xD2) & 0x00FF) | (place.win ? 0x0C00 : 0x0300));    /* WCTLB */
    } else {
        VDP2_REG(0xD2) = shadow(0xD2) & 0x00FF;                 /* WCTLB: no window for N3 */
    }
    VDP2_REG(0xEC) = shadow(0xEC) & ~0x0008;                    /* CCCTL: no N3 colour calculation */
    VDP2_REG(0xEA) = shadow(0xEA) & ~0x00C0;                    /* SFPRMD: N3 normal priority */
    VDP2_REG(0x110) = shadow(0x110) & ~0x0008;                  /* CLOFEN: no N3 colour offset */
    VDP2_REG(0xFA) = (shadow(0xFA) & 0x00FF) | 0x0700;          /* PRINB: N3 priority 7 */
    VDP2_REG(0xE4) = (u16)((shadow(0xE4) & 0x0FFF) | shl4(shl4(shl4(place.caos))));  /* CRAOFA */
    VDP2_REG(place.cyc_reg) = place.cyc_lo;
    VDP2_REG(place.cyc_reg + 2) = place.cyc_hi;
    /* sprites win priority ties, so sprite priority 7 would hide the text: lower it to 6
       while a line is shown, when that keeps sprites at or above both bitmap layers */
    u16 prina = shadow(0xF8);
    if ((prina & 7) < 7 && (shr4(shr4(prina)) & 7) < 7)
        for (int r = 0xF0; r <= 0xF6; r += 2) {
            u16 v = shadow(r);
            if ((v & 7) == 7)
                v = (u16)(v - 1);
            if ((v & 0x0700) == 0x0700)
                v = (u16)(v - 0x0100);
            VDP2_REG(r) = v;
        }
}

static const u16 restore_regs[] = { 0x20, 0x2A, 0x36, 0x3A, 0x3C, 0x4C, 0x4E, 0x94, 0x96, 0xD2, 0xEC, 0xEA,
                                    0x110, 0xFA, 0xE4, 0x10, 0x12, 0x14, 0x16, 0x18, 0x1A, 0x1C, 0x1E,
                                    0xF0, 0xF2, 0xF4, 0xF6, 0xC0, 0xC2, 0xC4, 0xC6, 0xC8, 0xCA, 0xCC, 0xCE };

static void n3_restore(void)
{
    /* take our scroll out of the library's copy, unless the game has rewritten it */
    if (set_scy3 && SHADOW(0x96) == (set_scy3 & 0x7FF))
        SHADOW(0x96) = 0;
    set_scy3 = 0;
    for (unsigned i = 0; i < sizeof restore_regs / 2; i++)
        VDP2_REG(restore_regs[i]) = shadow(restore_regs[i]);
}

static volatile u16 *n3_cram(void) { return CRAM16 + shl4(shl4(place.caos) + place.pal); }

static void n3_layout(void)
{
    volatile u16 *c = n3_cram();
    for (int i = 0; i < 4; i++)
        c[i] = col[i];
    volatile u16 *map = (volatile u16 *)(VRAM_BASE + place.map);
    if (place.win > 1) {                            /* page mode: everything else names the blank */
        volatile u32 *ch = (volatile u32 *)(VRAM_BASE + place.chars[N3_CHARS]);
        for (int j = 0; j < 32; j++)
            ch[j] = 0;
        volatile u16 *pg = map - 0x300;
        for (int i = 0; i < 1024; i++)
            pg[i] = n3_pn(N3_CHARS);
    }
    for (int i = 0; i < N3_CHARS; i++)              /* row 0: line 1, row 1 (+64 bytes): line 2 */
        map[i < 20 ? i : i + 12] = n3_pn(i);
    n3_cur = -2;
}

static int n3_layout_ok(void)
{
    volatile u16 *map = (volatile u16 *)(VRAM_BASE + place.map);
    return map[0] == n3_pn(0) && map[32 + 19] == n3_pn(N3_CHARS - 1) && n3_cram()[3] == col[3];
}

/* zero what we wrote (it was zero, or outside every bitmap), and our colours */
static void n3_wipe(void)
{
    if (n3_layout_ok()) {
        volatile u32 *m = (volatile u32 *)(VRAM_BASE + place.map - (place.win > 1 ? 0x600 : 0));
        for (int i = 0; i < (place.win > 1 ? 512 : 32); i++)
            m[i] = 0;
        for (int i = 0; i < N3_CHARS; i++) {
            volatile u32 *ch = (volatile u32 *)(VRAM_BASE + place.chars[i]);
            for (int j = 0; j < 32; j++)
                ch[j] = 0;
        }
    }
    volatile u16 *c = n3_cram();
    for (int i = 1; i < 4; i++)
        if (c[i] == col[i])
            c[i] = 0;
}

/* is the place still usable: every slot off the screen (the scene may scroll), and, when a
   line starts, still zero or ours inside a bitmap */
static int place_free(void)
{
    int ours = n3_layout_ok();
    if (slot_kind(place.map) == SLOT_SHOWN || (!ours && !slot_ok(place.map)))
        return 0;
    for (int i = 0; i < N3_CHARS; i++)
        if (slot_kind(place.chars[i]) == SLOT_SHOWN || (!ours && !slot_ok(place.chars[i])))
            return 0;
    return 1;
}

/* one line of glyphs into the cell buffer: line 0 = cells 0..79, line 1 = 80..159 */
static void n3_line(int line, const u16 *text, int n, int x)
{
    const struct group_header *h = (const struct group_header *)DATA;
    const struct glyph *gl = (const struct glyph *)(DATA + h->glyphs);
    u8 *buf = (u8 *)CELLBUF;
    int base = line ? 2 * N3_COLS : 0;
    for (int i = 0; i < n; i++) {
        const struct glyph *g = gl + text[i];
        int w = g->width, pitch = shr2(w + 3);
        const u8 *bits = DATA + h->bits + g->bits;
        for (int y = 0; y < GLYPH_ROWS; y++, bits += pitch) {
            int rowcell = base + (y < 8 ? 0 : N3_COLS);
            int yoff = shl2(y & 7);
            for (int px = 0; px < w; px++) {
                u32 b = bits[shr2(px)];
                switch (px & 3) {
                case 0: b = shr6(b); break;
                case 1: b = shr4(b); break;
                case 2: b = shr2(b); break;
                }
                b &= 3;
                if (!b)
                    continue;
                int sx = x + px;
                if (sx < 0 || sx >= 320)
                    continue;
                u8 *p = buf + shl5(rowcell + shr3(sx)) + yoff + shr1(sx & 7);
                if (sx & 1)
                    *p = (u8)((*p & 0xF0) | b);
                else
                    *p = (u8)((*p & 0x0F) | shl4(b));
            }
        }
        x += w;
    }
}

static void n3_draw(int idx)
{
    for (int i = 0; i < N3_CELLS * 8; i++)
        CELLBUF[i] = 0;
    if (idx >= 0) {
        const struct group_header *h = (const struct group_header *)DATA;
        const struct cue *c = (const struct cue *)(DATA + h->cues) + idx;
        const u16 *text = (const u16 *)(DATA + h->text) + c->text;
        if (c->n1)
            n3_line(0, text, c->n1, c->x1);
        if (c->n2)
            n3_line(1, text + c->n1, c->n2, c->x2);
    }
    /* character i = line i / 20, 16x16 piece i % 20: cells top-left, top-right, bottom-left,
       bottom-right of the cell buffer (40 cells per 8-pixel row, two rows per line) */
    for (int i = 0; i < N3_CHARS; i++) {
        int line = i < 20 ? 0 : 1, c = i < 20 ? i : i - 20;
        int top = line ? 2 * N3_COLS : 0;
        static const u8 corner[4][2] = { { 0, 0 }, { 0, 1 }, { 1, 0 }, { 1, 1 } };
        volatile u32 *dst = (volatile u32 *)(VRAM_BASE + place.chars[i]);
        for (int q = 0; q < 4; q++) {
            const u32 *src = CELLBUF + shl_n(top + (corner[q][0] ? N3_COLS : 0) + 2 * c + corner[q][1], 3);
            for (int j = 0; j < 8; j++)
                *dst++ = src[j];
        }
    }
    n3_cur = idx;
}

static int cue_now(u32 now)
{
    if (cur_voice < 0 || !started || loaded_group < 0)
        return -1;
    const struct group_header *h = (const struct group_header *)DATA;
    const struct cue *c = (const struct cue *)(DATA + h->cues);
    u32 t = now - anchor;
    for (int i = 0; i < h->ncues; i++)
        if (c[i].voice == cur_voice && t >= c[i].start && t < c[i].end)
            return i;
    return -1;
}

static int active = 0;

static void deactivate(void)
{
    if (active) {
        n3_restore();
        n3_wipe();
    }
    active = 0;
    n3_cur = -2;
}

void sub_on_vblank(void)        /* vblank callback, right after the VDP2 register upload */
{
    u32 now = VBLANKS;
    watch_keyon(now);
#ifndef LOG
    int idx = cue_now(now);
    if (idx < 0) {
        deactivate();
        return;
    }
    if (!placed || !sig_same() || (!active && !place_free())) {
        deactivate();
        relayouts = 0;
        if (!choose_place() || !place_free())
            return;
    }
    if (!place_free()) {                        /* the scene scrolled onto our slots */
        deactivate();
        placed = 0;
        return;
    }
    if (!active) {
        active = 1;
        n3_layout();
    } else if (!n3_layout_ok()) {
        if (++relayouts > 3) {      /* the scene keeps writing there: give up for this voice */
            deactivate();
            placed = 0;
            cur_voice = -1;
            return;
        }
        n3_layout();
    }
    n3_regs();
    if (idx != n3_cur)
        n3_draw(idx);
#endif
}
