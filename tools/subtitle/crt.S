/* SUB.BIN entry points, loaded at BASE = 0x060F6800 by the stub in the executable "0".

   The stub replaces every call of the file loader 0x06006874(name, dest).  On the first call
   it reads SUB.BIN through the loader itself (the CD is idle then), purges the cache and
   jumps to BASE+0x00 with the caller's r4/r5/pr intact; later calls jump straight there.

   BASE+0x00  load hook:   file loader 0x06006874(name, dest)
   BASE+0x10  magic "KHSU" (the stub checks it)
   BASE+0x14  vblank hook: the vblank callback's call of 0x0608C9E4 (VDP1/VDP2 library
              frame end, which uploads the VDP2 registers from their RAM copies)
   BASE+0x18  play hook:   voice player 0x06055C80(name)
   BASE+0x1C  play hook:   its wrapper 0x06055E88(name) (sets a flag, then calls the player)
   The last three are installed in RAM by SUB.BIN itself on its first load hook call.
*/
        .section .text.entry, "ax"
        .global _start
_start:
        bra     load_hook
        nop
        .long   0, 0, 0                 /* pad to BASE+0x10 */
        .long   0x4B485355              /* BASE+0x10: magic "KHSU" */
        bra     vblank_hook             /* BASE+0x14 */
        nop
        bra     play_hook               /* BASE+0x18 */
        nop
        bra     play2_hook              /* BASE+0x1C */
        nop

load_hook:
        sts.l   pr, @-r15
        mov.l   r4, @-r15
        mov.l   r5, @-r15
        mov.l   .Lon_load, r0
        jsr     @r0                     /* sub_on_load(name) */
        nop
        mov.l   @r15+, r5
        mov.l   @r15+, r4
        lds.l   @r15+, pr
        mov.l   .Lorig_load, r0
        jmp     @r0                     /* tail call: original loader(name, dest) */
        nop

play_hook:
        sts.l   pr, @-r15
        mov.l   r4, @-r15
        mov.l   .Lon_play, r0
        jsr     @r0                     /* sub_on_play(name) */
        nop
        mov.l   @r15+, r4
        lds.l   @r15+, pr
        mov.l   .Lorig_play, r0
        jmp     @r0                     /* tail call: original voice player(name) */
        nop

play2_hook:
        sts.l   pr, @-r15
        mov.l   r4, @-r15
        mov.l   .Lon_play, r0
        jsr     @r0                     /* sub_on_play(name) */
        nop
        mov.l   @r15+, r4
        lds.l   @r15+, pr
        mov.l   .Lorig_play2, r0
        jmp     @r0                     /* tail call: the player's wrapper(name) */
        nop

vblank_hook:
        sts.l   pr, @-r15
        mov.l   .Lorig_vblank, r0
        jsr     @r0                     /* library frame end + VDP2 register upload */
        nop
        mov.l   .Lon_vblank, r0
        jsr     @r0                     /* sub_on_vblank(): our NBG3 fields on top */
        nop
        lds.l   @r15+, pr
        rts
        nop

        .align  2
.Lon_load:      .long   sub_on_load
.Lorig_load:    .long   0x06006874
.Lon_play:      .long   sub_on_play
.Lorig_play:    .long   0x06055C80
.Lorig_play2:   .long   0x06055E88
.Lorig_vblank:  .long   0x0608C9E4
.Lon_vblank:    .long   sub_on_vblank
