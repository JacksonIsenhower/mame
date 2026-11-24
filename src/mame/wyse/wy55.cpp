// license:BSD-3-Clause
// copyright-holders:AJR
/***********************************************************************************************************************************

    Driver for Wyse WY-55 and related terminals.

    The WY-55's custom video gate array is numbered 211019-05. The WY-185 is believed to run on similar hardware, though with
    85 Hz and 60 Hz vertical refresh rates rather than 80 Hz and 70 Hz.

    WY-65's "PELVIS" ASIC (QFP160) supports refresh rates up to 94 Hz.

    WY-55 hardware, as used by the v2.1 firmware:

    - 80C32 at 14.7456 MHz. P1.2 selects which 64K half of the 128K program ROM is fetched from.
    - External data space:
        0000-1FFF   8K SRAM (battery backed): character plane, setup, buffers
        8000-9FFF   8K SRAM (battery backed): attribute plane (character address | 8000)
        A000-BFFF   8K SRAM: font RAM
        C000-DFFF   gate array registers, decoded on A8-A12 and write-only; the firmware keeps a shadow copy of
                    registers C0-DF at 1F98-1FB7
        F018-F01A   16C452 parallel port (data, status, control)
        F028-F02F   16C452 UART A, aux/printer port (interrupt on INT1)
        F030-F037   16C452 UART B, host port (interrupt on INT0)
    - Each vertical retrace is signalled on UART B's RI input; the firmware's per-frame work runs from the modem status
      interrupt. Every frame it rebuilds a table of row start addresses, two bytes (high, low) per row, at 1F00.
    - Keyboard: the standard Wyse serial-scan keyboard. A write to DC pulses the keyboard command line, advancing the
      key counter, and leaves it active; writes to DD leave it idle, and enough of them in a row let the counter reset. T0 reads the addressed
      key, inverted (high when it is down); T1 tells whether the last of those writes was to DD. On blank NVRAM the
      firmware waits for SetUp to be pressed.

***********************************************************************************************************************************/

#include "emu.h"
#include "bus/rs232/rs232.h"
#include "bus/wysekbd/wysekbd.h"
#include "cpu/mcs51/i80c52.h"
#include "machine/ins8250.h"
#include "machine/nvram.h"
#include "machine/scn_pci.h"
#include "sound/beep.h"
#include "screen.h"
#include "speaker.h"

#define LOG_GA      (1U << 1)
#define LOG_ROWS    (1U << 2)

//#define VERBOSE (LOG_GA | LOG_ROWS)
#include "logmacro.h"

namespace {

class wy55_state : public driver_device
{
public:
	wy55_state(const machine_config &mconfig, device_type type, const char *tag)
		: driver_device(mconfig, type, tag)
		, m_maincpu(*this, "maincpu")
		, m_epci(*this, "epci")
		, m_uart(*this, "uart%c", 'a')
		, m_host(*this, "host")
		, m_aux(*this, "aux")
		, m_keyboard(*this, "keyboard")
		, m_screen(*this, "screen")
		, m_beeper(*this, "beeper")
		, m_progbank(*this, "progbank")
		, m_charram(*this, "nvram0")
		, m_attrram(*this, "nvram1")
		, m_fontram(*this, "fontram")
	{
	}

	void wy55(machine_config &config) ATTR_COLD;
	void wy65(machine_config &config) ATTR_COLD;
	void wy185es(machine_config &config) ATTR_COLD;

	void init_wy55() ATTR_COLD;

protected:
	virtual void machine_start() override ATTR_COLD;
	virtual void machine_reset() override ATTR_COLD;

private:
	void wy65_progbank_w(u8 data) { m_progbank->set_entry(data & 0x03); }
	u8 epci_r(offs_t offset) { return m_epci->read(offset >> 10); }
	void epci_w(offs_t offset, u8 data) { m_epci->write(offset >> 10, data); }

	u8 ga_r(offs_t offset);
	void ga_w(offs_t offset, u8 data);
	u8 p3_r();
	void update_geometry();
	int cell_height() const { return (m_ga[0x13] >= 7 && m_ga[0x13] <= 15) ? m_ga[0x13] + 1 : 16; }
	int rows() const { return cell_height() <= 10 ? 44 : 26; }
	void vblank_w(int state);

	u32 screen_update(screen_device &screen, bitmap_rgb32 &bitmap, const rectangle &cliprect);

	void wy55_base(machine_config &config) ATTR_COLD;
	void prog_map(address_map &map) ATTR_COLD;
	void ext_map(address_map &map) ATTR_COLD;
	void wy65_ext_map(address_map &map) ATTR_COLD;
	void wy185es_ext_map(address_map &map) ATTR_COLD;

	required_device<mcs51_cpu_device> m_maincpu;
	optional_device<scn_pci_device> m_epci;
	optional_device_array<ns16450_device, 2> m_uart;
	optional_device<rs232_port_device> m_host;
	optional_device<rs232_port_device> m_aux;
	optional_device<wyse_keyboard_port_device> m_keyboard;
	required_device<screen_device> m_screen;
	optional_device<beep_device> m_beeper;
	required_memory_bank m_progbank;
	optional_shared_ptr<u8> m_charram;
	optional_shared_ptr<u8> m_attrram;
	optional_shared_ptr<u8> m_fontram;

	bool m_kbd_t1 = true;
	u8 m_ga[0x20]{};
};


void wy55_state::machine_start()
{
	memory_region *rgn = memregion("program");
	m_progbank->configure_entries(0, rgn->bytes() / 0x10000, rgn->base(), 0x10000);
	m_progbank->set_entry(0);

	save_item(NAME(m_kbd_t1));
	save_item(NAME(m_ga));
}

void wy55_state::machine_reset()
{
	// Port 1 resets high, so execution starts in the upper half of the ROM
	if (memregion("program")->bytes() == 0x20000)
		m_progbank->set_entry(1);
}

u8 wy55_state::ga_r(offs_t offset)
{
	if (!machine().side_effects_disabled())
		LOGMASKED(LOG_GA, "%s: gate array read %02X\n", machine().describe_context(), 0xc0 + (offset >> 8));
	return 0xff;
}

void wy55_state::ga_w(offs_t offset, u8 data)
{
	const u8 reg = offset >> 8;
	const bool geometry = (reg == 0x01 || reg == 0x07 || reg == 0x13) && data != m_ga[reg];
	m_ga[reg] = data;
	if (geometry)
		update_geometry();

	switch (reg)
	{
	case 0x03:
		// Bits 6-7: bell volume (0 = silent). The firmware puts Setup's Bell Volume here for 19 frames on BEL, and 1
		// for 2 frames as the keyclick. Bits 0-4 are the smooth scroll offset, used by screen_update.
		if (m_beeper)
		{
			m_beeper->set_output_gain(ALL_OUTPUTS, (data >> 6) / 3.0);
			m_beeper->set_state((data & 0xc0) != 0);
		}
		break;

	case 0x1c:
		// Pulse the keyboard command line and leave it active (T1 reads it back low): the keyboard advances its key
		// counter on each rising edge. The firmware scans with interrupts enabled, so the line has to stay active
		// between these writes, or an interrupt in the middle of a scan would let the counter time out and the rest
		// of the scan would read the wrong keys (held keys seen as released, so they repeat at once).
		if (!m_kbd_t1)
			m_keyboard->cmd_w(0);
		m_keyboard->cmd_w(1);
		m_kbd_t1 = false;
		break;

	case 0x1d:
		// Leave the line idle; the firmware writes this repeatedly to let the keyboard's counter time out and reset
		m_keyboard->cmd_w(0);
		m_kbd_t1 = true;
		break;

	default:
		LOGMASKED(LOG_GA, "%s: gate array register %02X = %02X\n", machine().describe_context(), 0xc0 + reg, data);
		break;
	}
}

void wy55_state::update_geometry()
{
	// C7 bit 7 selects 80 columns, which use a 2/3 dot clock with the same line rate. D3 is the character cell
	// height - 1: Setup's Char Cell gives 16, 15 or 13-line cells for 26 rows (status line, 24/25 lines, label
	// line), and the 42/43-line layout of 44 rows uses 9-line cells.
	const bool c80 = BIT(m_ga[0x07], 7);
	const int lines = std::min(rows() * cell_height(), 440);
	m_screen->configure(c80 ? 1050 : 1575, 448, rectangle(0, (c80 ? 800 : 1188) - 1, 0, lines - 1), m_screen->frame_period());
}

u8 wy55_state::p3_r()
{
	// P3.4 (T0) <- keyboard data, inverted: high when the addressed key is down. The firmware reads the keyboard ID
	// MSB first and knows 82 (ASCII), 41 (Enhanced PC) and C1. P3.5 (T1) is high after a DD write and low after a DC
	// strobe, which the keyboard self-test checks.
	return 0xcf | (m_keyboard->data_r() ? 0x00 : 0x10) | (m_kbd_t1 ? 0x20 : 0x00);
}

void wy55_state::vblank_w(int state)
{
	// Retrace drives UART B's ring indicator; its trailing edge, at the start of blanking, interrupts
	m_uart[1]->ri_w(state ? 1 : 0);
}

u32 wy55_state::screen_update(screen_device &screen, bitmap_rgb32 &bitmap, const rectangle &cliprect)
{
	bitmap.fill(rgb_t::black(), cliprect);
	if (!m_charram)
		return 0;

	// Each frame the firmware lists the rows' start addresses (high byte first) at 1F00: the status line, then the
	// page. A row is a header byte followed by its characters; attributes sit at the same address in the second
	// RAM (gate array C5 bit 3), or for the personalities without one (WY-50, TVI, ADDS, Hazeltine) are embedded in
	// the character plane as field attribute bytes 80-9F that apply until the next one. Characters A0-FF are
	// protected characters, shown with the attribute in C0 (bit 6 = dim, bits 0-4 as below).
	// Attribute bits: 0-1 intensity (1 = invisible, 2 = dim, 3 = bold), 2 underline, 3 reverse, 4 blink, 6-7 glyph bank (font RAM
	// holds 512 glyphs: bit 6 selects 80-FF, which hold the line drawing and multinational characters). The cursor
	// row has bit 3 of its header set, and gate array C9 holds the cursor's column + 1. Header bit 7 makes the row
	// double width (the firmware stores each character in both cells of a pair), bit 6 double height, with bit 4
	// set for the top half.
	static constexpr rgb_t PALETTE[4] = { rgb_t(0xd0, 0xd0, 0xc8), rgb_t::black(), rgb_t(0x80, 0x80, 0x7c), rgb_t(0xff, 0xff, 0xf8) };
	const bool c80 = BIT(m_ga[0x07], 7);
	const bool embedded = !BIT(m_ga[0x05], 3);
	const u8 protect = (m_ga[0x00] & 0x1f) | (BIT(m_ga[0x00], 6) ? 0x02 : 0x00);
	const int cols = c80 ? 80 : 132;
	const int cw = c80 ? 10 : 9;
	// Cells are 16, 15 or 13 lines in 24/25-line mode and 9 in 42/43-line mode, for which the firmware loads a
	// shorter font. D4 is the underline's scan line (the cell's last).
	const int ch = cell_height();
	const int underline = m_ga[0x14] < ch ? m_ga[0x14] : ch - 1;
	const bool blink_off = BIT(screen.frame_number(), 5);
	// C2 bit 7 blanks the screen (screen saver, and while the firmware rebuilds it); bit 6 is the light (reverse)
	// screen of Setup's Display. C7 bit 0 shows the cursor (the firmware clears it at each frame and sets it again
	// after marking the cursor row, unless the cursor is off), bit 1 makes it blink and bit 2 makes it an underline
	// instead of a block.
	if (BIT(m_ga[0x02], 7))
		return 0;
	const bool light = BIT(m_ga[0x02], 6);
	const bool cursor_off = !BIT(m_ga[0x07], 0) || (BIT(m_ga[0x07], 1) && BIT(screen.frame_number(), 4));
	const bool cursor_line = BIT(m_ga[0x07], 2);
	u8 field = 0;

	// Smooth scrolling: C3 bits 0-4 are a scan-line offset. While a region scrolls, the firmware marks its first row
	// with header bits 0-1 = 2 and appends the incoming row, marked 3, after its last one (so the table has one
	// entry more than usual). The first row is shown from the offset down and the extra row only above it, so the
	// region keeps its height and moves by the offset.
	const int offset = m_ga[0x03] & 0x1f;
	int top = 0;
	for (int row = 0; row <= rows() && top <= cliprect.max_y; row++)
	{
		const u16 base = (m_charram[0x1f00 + row * 2] << 8 | m_charram[0x1f01 + row * 2]) & 0x1fff;
		const u8 header = m_charram[base];
		const bool dw = BIT(header, 7);
		const bool dh = BIT(header, 6);
		const int dh_base = BIT(header, 4) ? 0 : ch;
		const int first = (header & 3) == 2 ? std::min(offset, ch) : 0;
		const int last = (header & 3) == 3 ? std::min(offset, ch) : ch;
		if (row < 2)
			field = 0;
		for (int col = 0; col < cols; col++)
		{
			const u16 addr = (base + 1 + col) & 0x1fff;
			u8 code = m_charram[addr];
			u8 attr;
			if ((code & 0xe0) == 0x80)
			{
				field = code & 0x1f;
				attr = field;
				code = 0x20;
			}
			else
			{
				attr = embedded ? field : m_attrram[addr];
				if (BIT(code, 7))
				{
					code &= 0x7f;
					attr |= protect;
				}
			}

			const bool cursor = BIT(header, 3) && col == m_ga[0x09] - 1 && !cursor_off;
			const bool hidden = BIT(attr, 4) && blink_off;
			const rgb_t fg = PALETTE[attr & 3];
			const u16 glyph = code | BIT(attr, 6) << 7 | BIT(attr, 7) << 8;
			const bool linegfx = (glyph & 0x1e0) == 0x080;
			for (int y = first; y < last; y++)
			{
				const int sy = top + y - first;
				if (sy < cliprect.min_y || sy > cliprect.max_y)
					continue;
				const int fy = dh ? (dh_base + y) / 2 : y;
				u8 bits = hidden ? 0 : m_fontram[(glyph << 4 | fy) & 0x1fff];
				if (BIT(attr, 2) && fy == underline)
					bits = 0xff;
				const bool inv = (BIT(attr, 3) != (cursor && (!cursor_line || y == underline))) != light;
				for (int x = 0; x < cw; x++)
				{
					const int sx = col * cw + x;
					if (sx < cliprect.min_x || sx > cliprect.max_x)
						continue;
					const int gx = dw ? ((col & 1) * cw + x) / 2 : x;
					const bool on = (gx < 8 ? BIT(bits, 7 - gx) : linegfx ? BIT(bits, 0) : (BIT(attr, 2) && fy == underline)) != inv;
					bitmap.pix(sy, sx) = on ? (light && !BIT(attr, 3) ? PALETTE[0] : fg) : rgb_t::black();
				}
			}
		}
		top += last - first;
	}
	return 0;
}

void wy55_state::prog_map(address_map &map)
{
	map(0x0000, 0xffff).bankr("progbank");
}

void wy55_state::ext_map(address_map &map)
{
	map(0x0000, 0x1fff).ram().share("nvram0");
	map(0x8000, 0x9fff).ram().share("nvram1");
	map(0xa000, 0xbfff).ram().share("fontram");
	map(0xc000, 0xdfff).rw(FUNC(wy55_state::ga_r), FUNC(wy55_state::ga_w));
	map(0xf018, 0xf018).nopw();             // parallel data
	map(0xf019, 0xf019).lr8(NAME([] () -> u8 { return 0x07; }));    // parallel status: no printer
	map(0xf01a, 0xf01a).noprw();            // parallel control
	map(0xf028, 0xf02f).rw(m_uart[0], FUNC(ns16450_device::ins8250_r), FUNC(ns16450_device::ins8250_w));
	map(0xf030, 0xf037).rw(m_uart[1], FUNC(ns16450_device::ins8250_r), FUNC(ns16450_device::ins8250_w));
}

void wy55_state::wy185es_ext_map(address_map &map)
{
	map(0x0000, 0x1fff).ram().share("nvram0");
	map(0x8000, 0x9fff).ram().share("nvram1");
	map(0xa000, 0xbfff).ram().share("fontram");
	map(0xe000, 0xefff).r(FUNC(wy55_state::epci_r));
	map(0xf000, 0xffff).w(FUNC(wy55_state::epci_w));
}

void wy55_state::wy65_ext_map(address_map &map)
{
	map(0x0000, 0xdfff).ram();
	map(0xee02, 0xee02).w(FUNC(wy55_state::wy65_progbank_w));
}


static INPUT_PORTS_START(wy55)
INPUT_PORTS_END

void wy55_state::wy55_base(machine_config &config)
{
	I80C32(config, m_maincpu, 14.7456_MHz_XTAL);
	m_maincpu->set_addrmap(AS_PROGRAM, &wy55_state::prog_map);
	m_maincpu->set_addrmap(AS_DATA, &wy55_state::ext_map);
	m_maincpu->port_out_cb<1>().set_membank("progbank").bit(2);

	SCREEN(config, m_screen);
	m_screen->set_raw(49.4235_MHz_XTAL, 1575, 0, 1188, 448, 0, 416);
	//m_screen->set_raw(49.4235_MHz_XTAL * 2 / 3, 1050, 0, 800, 392, 0, 338);
	m_screen->set_screen_update(FUNC(wy55_state::screen_update));
}

void wy55_state::wy55(machine_config &config)
{
	wy55_base(config);
	m_maincpu->port_in_cb<3>().set(FUNC(wy55_state::p3_r));

	NVRAM(config, "nvram0", nvram_device::DEFAULT_ALL_0); // 8K SRAM + battery
	NVRAM(config, "nvram1", nvram_device::DEFAULT_ALL_0); // 8K SRAM + battery

	// 16C452: two 16450-compatible UARTs and a parallel port, clocked at 14.7456 MHz / 8
	// UART A is the aux (printer) port
	NS16450(config, m_uart[0], 14.7456_MHz_XTAL / 8);
	m_uart[0]->out_tx_callback().set(m_aux, FUNC(rs232_port_device::write_txd));
	m_uart[0]->out_dtr_callback().set(m_aux, FUNC(rs232_port_device::write_dtr));
	m_uart[0]->out_rts_callback().set(m_aux, FUNC(rs232_port_device::write_rts));
	m_uart[0]->out_int_callback().set_inputline(m_maincpu, MCS51_INT1_LINE);

	// UART B is the host (modem) port
	NS16450(config, m_uart[1], 14.7456_MHz_XTAL / 8);
	m_uart[1]->out_tx_callback().set(m_host, FUNC(rs232_port_device::write_txd));
	m_uart[1]->out_dtr_callback().set(m_host, FUNC(rs232_port_device::write_dtr));
	m_uart[1]->out_rts_callback().set(m_host, FUNC(rs232_port_device::write_rts));
	m_uart[1]->out_int_callback().set_inputline(m_maincpu, MCS51_INT0_LINE);

	RS232_PORT(config, m_host, default_rs232_devices, "null_modem");
	m_host->rxd_handler().set(m_uart[1], FUNC(ns16450_device::rx_w));
	m_host->cts_handler().set(m_uart[1], FUNC(ns16450_device::cts_w));
	m_host->dsr_handler().set(m_uart[1], FUNC(ns16450_device::dsr_w));
	m_host->dcd_handler().set(m_uart[1], FUNC(ns16450_device::dcd_w));

	RS232_PORT(config, m_aux, default_rs232_devices, nullptr);
	m_aux->rxd_handler().set(m_uart[0], FUNC(ns16450_device::rx_w));
	m_aux->cts_handler().set(m_uart[0], FUNC(ns16450_device::cts_w));
	m_aux->dsr_handler().set(m_uart[0], FUNC(ns16450_device::dsr_w));
	m_aux->dcd_handler().set(m_uart[0], FUNC(ns16450_device::dcd_w));

	WYSE_KEYBOARD(config, m_keyboard, wy60_keyboards, "ascii");

	m_screen->screen_vblank().set(FUNC(wy55_state::vblank_w));

	// The gate array makes the bell and keyclick; the tone hasn't been measured
	SPEAKER(config, "speaker").front_center();
	BEEP(config, m_beeper, 1000).add_route(ALL_OUTPUTS, "speaker", 0.30);
}

void wy55_state::wy185es(machine_config &config)
{
	wy55_base(config);
	m_maincpu->set_clock(11_MHz_XTAL);
	m_maincpu->set_addrmap(AS_DATA, &wy55_state::wy185es_ext_map);
	m_maincpu->port_out_cb<1>().set_nop();

	NVRAM(config, "nvram0", nvram_device::DEFAULT_ALL_0); // 8K SRAM + battery
	NVRAM(config, "nvram1", nvram_device::DEFAULT_ALL_0); // 8K SRAM + battery

	SCN2661B(config, m_epci, 49.4235_MHz_XTAL / 10); // SCN2661BC1N28
	m_epci->rxrdy_handler().set_inputline(m_maincpu, MCS51_INT1_LINE);
}

void wy55_state::wy65(machine_config &config)
{
	DS80C320(config, m_maincpu, 58.9824_MHz_XTAL / 4); // divider uncertain
	m_maincpu->set_addrmap(AS_PROGRAM, &wy55_state::prog_map);
	m_maincpu->set_addrmap(AS_DATA, &wy55_state::wy65_ext_map);

	// TODO: NVRAM? (4x W24257S-70LL on board)

	SCREEN(config, m_screen);
	m_screen->set_raw(58.9824_MHz_XTAL, 1575, 0, 1188, 448, 0, 416); // dimensions probably wrong
	m_screen->set_screen_update(FUNC(wy55_state::screen_update));
}


ROM_START(wy55)
	ROM_REGION(0x20000, "program", 0)
	ROM_LOAD("251352-12.bin", 0x00000, 0x20000, CRC(efe41862) SHA1(52ee76d636b166fa10a37356aef81011a9b079cc)) // v2.1
ROM_END

// Locally patched v2.1 firmware (tmux-wy55's tools/wy55-patch.py writes it as build/251352-12.bin, scrambled like
// the chips). It has a name of its own so the parent's ROM is never loaded in its place, and no hash since it
// changes with every patch.
ROM_START(wy55p)
	ROM_REGION(0x20000, "program", 0)
	ROM_LOAD("wy55p.bin", 0x00000, 0x20000, NO_DUMP)
ROM_END

ROM_START(wy65)
	ROM_REGION(0x40000, "program", 0)
	ROM_LOAD("251455-03.bin", 0x00000, 0x40000, CRC(2afbf73b) SHA1(5a29b78ef377a6e2f2f91ff42a7e4d86eb511a5f)) // v2.1
ROM_END

ROM_START(wy185es)
	ROM_REGION(0x10000, "program", 0)
	ROM_LOAD("251201-03.bin", 0x00000, 0x10000, CRC(5b8cace5) SHA1(484bba8244a99edb80d7f7a5437c2be52c980fc1)) // v2.0
ROM_END

void wy55_state::init_wy55()
{
	memory_region *rgn = memregion("program");
	uint8_t *rom = rgn->base();

	for (offs_t base = 0x00000; base < rgn->bytes(); base += 0x4000)
	{
		std::vector<uint8_t> orig(&rom[base], &rom[base + 0x4000]);

		for (offs_t offset = 0; offset < 0x4000; offset++)
			rom[base | offset] = bitswap<8>(orig[bitswap<14>(offset, 3, 8, 2, 0, 7, 4, 9, 10, 11, 12, 13, 5, 1, 6)], 3, 4, 5, 2, 6, 1, 7, 0);
	}
}

} // anonymous namespace

COMP(1991, wy185es, 0, 0, wy185es, wy55, wy55_state, init_wy55, "Wyse Technology", "WY-185ES (v2.0)", MACHINE_NO_SOUND | MACHINE_NOT_WORKING)
COMP(1993, wy55,    0, 0, wy55,    wy55, wy55_state, init_wy55, "Wyse Technology", "WY-55 (v2.1)", MACHINE_IMPERFECT_SOUND | MACHINE_NOT_WORKING)
COMP(1993, wy55p,   wy55, 0, wy55, wy55, wy55_state, init_wy55, "Wyse Technology", "WY-55 (v2.1, patched)", MACHINE_IMPERFECT_SOUND | MACHINE_NOT_WORKING)
COMP(1996, wy65,    0, 0, wy65,    wy55, wy55_state, init_wy55, "Wyse Technology", "WY-65 (v2.1)", MACHINE_NO_SOUND | MACHINE_NOT_WORKING)
