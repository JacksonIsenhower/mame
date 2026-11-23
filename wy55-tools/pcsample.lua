-- Print the 80C32's PC, bank and a few state bytes every 10 frames (debugging aid)
local count = 0
local frames = tonumber(os.getenv("WY55_FRAMES") or "600")
emu.register_frame_done(function()
	count = count + 1
	local cpu = manager.machine.devices[":maincpu"]
	if count % 10 == 0 then
		local id = cpu.spaces["idata"]
		print(string.format("frame %4d PC=%04X P1=%02X SP=%02X $23=%02X $26=%02X ring %02X/%02X", count,
			cpu.state["PC"].value, cpu.state["P1"] and cpu.state["P1"].value or 0, id:read_u8(0x81) and cpu.state["SP"].value,
			id:read_u8(0x23), id:read_u8(0x26), id:read_u8(0x14), id:read_u8(0x15)))
	end
	if count == frames then manager.machine:exit() end
end)
