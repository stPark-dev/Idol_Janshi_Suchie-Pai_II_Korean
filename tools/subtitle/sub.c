/* Voice subtitles (SUB.BIN) for Idol Janshi Suchie-Pai II, Saturn, disc 1.

   SUB.BIN = this code (up to 0x1400 bytes) + the index of groups and trigger files appended by
   the build at BASE+0x1400.  It is read once by the stub in the executable on the first
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
#define INDEX       ((const u8 *)0x060F7C00)    /* BASE + 0x1400, appended by the build */
#define DATA        ((const u8 *)0x060F9000)    /* group data, up to GROUP_MAX bytes */
#define GROUP_MAX   0x5800
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
/* index (BASE+0x1400): header, ngroups x {byte offset in SUBDAT.BIN, packed and unpacked sizes}, then the
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
#define N3_ROWS     4               /* two text lines, two cell rows each */
#define N3_COLS     40
#define N3_CELLS    (N3_ROWS * N3_COLS)
#define N3_ROW      25              /* first map row of the two text lines: y 200..231 */
#define N3_FOOT     (0x2000 + 32 + N3_CELLS * 32)   /* bytes of the block we write */
#define GLYPH_ROWS  16

/* library RAM copies of the VDP2 registers (verified against the chip in save states) */
static u16 shadow(int reg)
{
    if (reg < 0xE0)
        return *(volatile u16 *)(0x060B4E30 + reg);
    return *(volatile u16 *)(0x060B5D2C + reg - 0xE0 - (reg > 0xFE ? 2 : 0));
}


/* Where NBG3 goes in the current scene, chosen from the library's register copies: a 16 KB
   VRAM block outside every visible part of the scene's bitmaps and empty when first used,
   two free access slots in that block's bank, and an unused colour RAM palette row.  Only
   scenes made of NBG0/NBG1 bitmaps (and VDP1 sprites) qualify: cell layers keep their
   character data anywhere in VRAM, so nothing can be shown to be free there. */
struct place {
    u32 block;                      /* map at block, characters at block + 0x2000 */
    u16 cyc_reg, cyc_lo, cyc_hi;    /* access pattern pair holding our two slots */
    u16 pal, caos;                  /* palette number and colour RAM offset */
    u16 sig[6];                     /* register copies the choice was made from */
};
static struct place place;
static int placed = 0;              /* place is valid for place.sig */
static int relayouts = 0;
static int n3_cur = -2;             /* cue in the cells, -1 = blank, -2 = unknown */

static const u16 sig_regs[6] = { 0x20, 0x28, 0x3C, 0x0E, 0x74, 0x84 };   /* BGON CHCTLA MPOFN RAMCTL SCYIN0/1 */

static int sig_same(void)
{
    for (int i = 0; i < 6; i++)
        if (place.sig[i] != shadow(sig_regs[i]))
            return 0;
    return 1;
}

/* is [lo, hi) outside the visible rows of bitmap layer n (0 or 1)?  strict: outside the
   whole bitmap */
static int bitmap_clear(int n, u32 lo, u32 hi, int strict)
{
    u32 ctl = shadow(0x28);
    if (n)
        ctl = shr_n(ctl, 8);
    if (!(ctl & 0x02))
        return 0;                                   /* cell layer: VRAM use unknown */
    int sz = shr2(ctl) & 3, cc = shr4(ctl) & 7;
    int sshift = ((sz & 2) ? 10 : 9) + (cc == 0 ? -1 : cc == 1 ? 0 : cc <= 3 ? 1 : 2);   /* log2 stride */
    int hrows = (sz & 1) ? 512 : 256;
    u32 base = (n ? shr4(shadow(0x3C)) : shadow(0x3C)) & 7;
    base = shl_n(base, 17);                         /* bitmap at map offset x 0x20000 */
    u32 size = shl_n((u32)hrows, sshift);
    if (hi <= base || lo >= base + size)
        return 1;
    if (strict)
        return 0;
    int sy = shadow(n ? 0x84 : 0x74) & (hrows - 1);
    u32 r0 = lo > base ? shr_n(lo - base, sshift) : 0;
    u32 r1 = shr_n(hi - 1 - base, sshift);
    if (r1 >= (u32)hrows)
        r1 = hrows - 1;
    for (u32 r = r0; r <= r1; r++)
        if (((r - sy) & (hrows - 1)) < 240)
            return 0;
    return 1;
}

static int block_empty(u32 blk)
{
    volatile u32 *v = (volatile u32 *)(VRAM_BASE + blk);
    for (int i = 0; i < N3_FOOT / 4; i++)
        if (v[i])
            return 0;
    return 1;
}

/* two adjacent unused slots T(k), T(k+1) in the pattern pair at reg (T0..T3, then T4..T7) */
static int pick_slots(u16 reg)
{
    u16 bg = shadow(0x20), ctl = shadow(0x28);
    u32 pat = shl_n(shadow(reg), 16) | shadow(reg + 2);
    for (int k = 0; k < 7; k++) {
        int ok = 1;
        for (int j = k; j <= k + 1; j++) {
            int c = shr_n(pat, 28 - shl2(j)) & 0xF;
            int unused = c == 0xF
                || (c == 0 && (!(bg & 1) || (ctl & 0x0002)))       /* NBG0 names: off or bitmap */
                || (c == 1 && (!(bg & 2) || (ctl & 0x0200)))       /* NBG1 names */
                || (c == 4 && !(bg & 1)) || (c == 5 && !(bg & 2))   /* characters of a layer that is off */
                || c == 2 || c == 3 || c == 6 || c == 7;           /* NBG2/NBG3 are off here */
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

static int choose_place(void)
{
    u16 bg = shadow(0x20), ramctl = shadow(0x0E);
    placed = 0;
    for (int i = 0; i < 6; i++)
        place.sig[i] = shadow(sig_regs[i]);
    if (bg & 0x3C)                                  /* NBG2, NBG3 or rotation layers in use */
        return 0;
    if (shadow(0x9A) || shadow(0xE8))               /* line/vertical cell scroll, line colour tables */
        return 0;
    /* first an empty block off the visible rows; else a block outside every bitmap, whatever
       an earlier scene left in it (the scenes keep nothing there between frames) */
    static const u32 blocks[] = { 0x7C000, 0x3C000, 0x5C000, 0x1C000 };
    for (unsigned i = 0; i < 2 * sizeof blocks / 4; i++) {
        int strict = i >= sizeof blocks / 4;
        u32 b = blocks[strict ? i - sizeof blocks / 4 : i];
        if ((bg & 1) && !bitmap_clear(0, b, b + N3_FOOT, strict))
            continue;
        if ((bg & 2) && !bitmap_clear(1, b, b + N3_FOOT, strict))
            continue;
        if (!strict && !block_empty(b))
            continue;
        int bank_b = b >= 0x40000;
        int second = (b & 0x20000) && (ramctl & (bank_b ? 0x0200 : 0x0100));
        if (!pick_slots((u16)((bank_b ? 0x18 : 0x10) + (second ? 4 : 0))))
            continue;
        if (!pick_cram())
            return 0;
        place.block = b;
        placed = 1;
        return 1;
    }
    return 0;
}

static u16 n3_blank(void)
{
    u32 ch = shr4(shr1(place.block + 0x2000));      /* character number = address / 32 */
    return (u16)(shl4(shl4(shl4(place.pal))) | (ch & 0x3FF));
}

static void n3_regs(void)
{
    u32 ch = shr4(shr1(place.block + 0x2000));
    u32 mp = shr4(shr4(shr4(shr1(place.block))));  /* map number = address / 0x2000 */
    VDP2_REG(0x20) = shadow(0x20) | 0x0008;                     /* BGON: N3ON */
    VDP2_REG(0x2A) = shadow(0x2A) & ~0x0030;                    /* CHCTLB: N3 1x1 cell, 16 colours */
    VDP2_REG(0x36) = (u16)(0x8000 | shr2(shr4(shr4(ch))));      /* PNCN3: 1 word, char bits 14..10 */
    VDP2_REG(0x3A) = shadow(0x3A) & ~0x00C0;                    /* PLSZ: N3 1x1 plane */
    VDP2_REG(0x3C) = shadow(0x3C) & ~0x7000;                    /* MPOFN: N3 map offset 0 */
    VDP2_REG(0x4C) = (u16)(mp * 0x0101);                        /* MPABN3 */
    VDP2_REG(0x4E) = (u16)(mp * 0x0101);                        /* MPCDN3 */
    VDP2_REG(0x94) = 0;                                         /* SCXIN3 */
    VDP2_REG(0x96) = 0;                                         /* SCYIN3 */
    VDP2_REG(0xD2) = shadow(0xD2) & 0x00FF;                     /* WCTLB: no N3 window */
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
                                    0xF0, 0xF2, 0xF4, 0xF6 };

static void n3_restore(void)
{
    for (unsigned i = 0; i < sizeof restore_regs / 2; i++)
        VDP2_REG(restore_regs[i]) = shadow(restore_regs[i]);
}

static volatile u16 *n3_cram(void) { return CRAM16 + shl4(shl4(place.caos) + place.pal); }

static void n3_layout(void)
{
    volatile u16 *c = n3_cram();
    for (int i = 0; i < 4; i++)
        c[i] = col[i];
    volatile u32 *ch = (volatile u32 *)(VRAM_BASE + place.block + 0x2000);
    for (int i = 0; i < 8; i++)
        ch[i] = 0;                                              /* first char: clear */
    u16 blank = n3_blank();
    volatile u16 *map = (volatile u16 *)(VRAM_BASE + place.block);
    for (int i = 0; i < 64 * 64; i++)
        map[i] = blank;
    for (int r = 0; r < N3_ROWS; r++)
        for (int k = 0; k < N3_COLS; k++)
            map[shl6(N3_ROW + r) + k] = (u16)(blank + 1 + r * N3_COLS + k);
    n3_cur = -2;
}

static int n3_layout_ok(void)
{
    volatile u16 *map = (volatile u16 *)(VRAM_BASE + place.block);
    u16 blank = n3_blank();
    return map[0] == blank && map[shl6(N3_ROW)] == blank + 1 && n3_cram()[3] == col[3];
}

/* clear what we wrote, so that later scenes find the block and the palette row empty again */
static void n3_wipe(void)
{
    if (n3_layout_ok()) {
        volatile u32 *v = (volatile u32 *)(VRAM_BASE + place.block);
        for (int i = 0; i < N3_FOOT / 4; i++)
            v[i] = 0;
    }
    volatile u16 *c = n3_cram();
    for (int i = 1; i < 4; i++)
        if (c[i] == col[i])
            c[i] = 0;
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
    volatile u32 *ch = (volatile u32 *)(VRAM_BASE + place.block + 0x2000) + 8;     /* chars 1.. */
    for (int i = 0; i < N3_CELLS * 8; i++)
        ch[i] = CELLBUF[i];
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
    if (!placed || !sig_same()) {
        deactivate();
        relayouts = 0;
        if (!choose_place())
            return;
    }
    if (!active) {
        if (!block_empty(place.block) && !choose_place())
            return;                 /* same setup, but this scene uses the block */
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
