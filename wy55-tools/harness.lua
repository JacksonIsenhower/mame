-- Test harness for the emulated WY-55 (run with -autoboot_script).
-- Environment:
--   WY55_FRAMES  frames to run before dumping and exiting (default 1500; 0 = run until closed, no dump)
--   WY55_SETUP   comma-separated frames at which to press SetUp for 6 frames (blank NVRAM waits for it)
--   WY55_KEYS    comma-separated FRAME:KEY presses, KEY being an input field name such as A or Return
--   WY55_POKE    comma-separated FRAME:ADDR:VALUE writes to internal RAM (hex ADDR and VALUE), e.g. 300:7b:25
--   WY55_SNAPS   comma-separated frames at which to save a snapshot as well
--   WY55_BAUD    host line speed for MAME's null modem (both directions), e.g. 115200 to match the Setup
--   WY55_FLOW    off, dtr or rts: the host side stops sending while the terminal holds that line off, as a host
--                with CTS flow control does through a cable bringing the terminal's DTR or RTS to its CTS
--   WY55_LINES   1 to log the host UART's DTR and RTS each time they change, as "frame N: DTR=.. RTS=.."
--   WY55_OUT     directory for the dump: ram0/ram1/font/iram .bin, personality.log, snapshot (default .)
local out = os.getenv("WY55_OUT") or "."
local frames = tonumber(os.getenv("WY55_FRAMES") or "1500")
local presses = {}
for f in string.gmatch(os.getenv("WY55_SETUP") or "", "%d+") do
	presses[#presses + 1] = { tonumber(f), "SetUp" }
end
for f, k in string.gmatch(os.getenv("WY55_KEYS") or "", "(%d+):([^,]+)") do
	presses[#presses + 1] = { tonumber(f), k }
end
local snaps = {}
for f in string.gmatch(os.getenv("WY55_SNAPS") or "", "%d+") do
	snaps[tonumber(f)] = true
end
local pokes = {}
for f, a, v in string.gmatch(os.getenv("WY55_POKE") or "", "(%d+):(%x+):(%x+)") do
	pokes[#pokes + 1] = { tonumber(f), tonumber(a, 16), tonumber(v, 16) }
end

local names = {
	[0x14] = "WYSE 50", [0x04] = "WYSE 50+", [0x2c] = "WYSE 60", [0x34] = "WYSE 120+", [0x24] = "WYSE 120",
	[0x35] = "WYSE 150+", [0x25] = "WYSE 150", [0x06] = "TVI 910+", [0x07] = "TVI 925", [0x17] = "TVI 905",
	[0x67] = "PC TERM", [0x01] = "ADDS A2", [0x00] = "HZ 1500", [0xa0] = "VT 52", [0xa8] = "VT 100",
	[0xac] = "VT 220-7", [0xbc] = "VT 220-8", [0xad] = "UNIX CONSOLE",
}

-- The null modem's TX/RX Baud settings (PORT_RS232_BAUD values)
local bauds = { [110] = 0x00, [150] = 0x01, [300] = 0x02, [600] = 0x03, [1200] = 0x04, [2400] = 0x05, [4800] = 0x06,
	[9600] = 0x07, [19200] = 0x09, [38400] = 0x0b, [57600] = 0x0c, [115200] = 0x0d }
local baud = tonumber(os.getenv("WY55_BAUD") or "")
local flow = ({ off = 0, rts = 1, dtr = 2 })[os.getenv("WY55_FLOW") or ""]
local watch_lines = os.getenv("WY55_LINES") == "1"
local last_mcr = -1

local log = io.open(out .. "/personality.log", "w")
local count, last = 0, -1

local function key_field(name)
	for tag, port in pairs(manager.machine.ioport.ports) do
		local field = port.fields[name]
		if field then return field end
	end
	error("no input field named " .. name)
end

local function dump_space(space, from, to, name)
	local f = io.open(out .. "/" .. name .. ".bin", "wb")
	for a = from, to do
		f:write(string.char(space:read_u8(a)))
	end
	f:close()
end

emu.register_frame_done(function()
	local m = manager.machine
	local cpu = m.devices[":maincpu"]
	local idata = cpu.spaces["idata"]
	count = count + 1

	if count == 1 and baud then
		local port = m.ioport.ports[":host:null_modem:RS232_TXBAUD"]
		port.fields["TX Baud"].user_value = bauds[baud]
		m.ioport.ports[":host:null_modem:RS232_RXBAUD"].fields["RX Baud"].user_value = bauds[baud]
	end
	if count == 1 and flow then
		m.ioport.ports[":host:null_modem:FLOW_CONTROL"].fields["Flow Control"].user_value = flow
	end

	if watch_lines then
		-- UART B's modem control register: bit 0 DTR, bit 1 RTS (reading it has no side effects)
		local mcr = cpu.spaces["xdata"]:read_u8(0xf034) & 3
		if mcr ~= last_mcr then
			log:write(string.format("frame %d: DTR=%s RTS=%s\n", count, mcr & 1 ~= 0 and "on" or "off",
				mcr & 2 ~= 0 and "on" or "off"))
			last_mcr = mcr
		end
	end

	for _, p in ipairs(presses) do
		if count == p[1] or count == p[1] + 6 then
			key_field(p[2]):set_value(count == p[1] and 1 or 0)
		end
	end

	for _, p in ipairs(pokes) do
		if count == p[1] then
			idata:write_u8(p[2], p[3])
		end
	end

	if snaps[count] then
		m.video:snapshot()
		-- Smooth-scroll state for the snapshot: C3 and the row table entries whose header bits 0-1 are set
		local xdata = cpu.spaces["xdata"]
		local line = string.format("frame %d: snapshot, C3=%02X", count, xdata:read_u8(0x1f9b))
		for i = 0, 44 do
			local base = (xdata:read_u8(0x1f00 + 2 * i) << 8 | xdata:read_u8(0x1f01 + 2 * i)) & 0x1fff
			local h = xdata:read_u8(base)
			if h & 3 ~= 0 then line = line .. string.format(" row%d=%02X", i, h) end
		end
		log:write(line .. "\n")
	end

	local p = idata:read_u8(0x23)
	if p ~= last then
		log:write(string.format("frame %d: $23=%02X %s\n", count, p, names[p] or "?"))
		log:flush()
		last = p
	end

	if count == frames then
		local xdata = cpu.spaces["xdata"]
		dump_space(xdata, 0x0000, 0x1fff, "ram0")
		dump_space(xdata, 0x8000, 0x9fff, "ram1")
		dump_space(xdata, 0xa000, 0xbfff, "font")
		dump_space(idata, 0x00, 0xff, "iram")
		log:close()
		m.video:snapshot()
		m:exit()
	end
end)
