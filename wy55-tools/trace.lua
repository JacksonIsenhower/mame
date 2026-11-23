-- Trace the 80C32 between two frames (needs -debug). Env: WY55_TRACE (file), WY55_FROM, WY55_TO
local from = tonumber(os.getenv("WY55_FROM") or "200")
local to = tonumber(os.getenv("WY55_TO") or "300")
local file = os.getenv("WY55_TRACE") or "trace.txt"
local count = 0
emu.register_frame_done(function()
	count = count + 1
	local dbg = manager.machine.debugger
	if count == 1 then dbg:command("go") end
	if count == from then dbg:command("trace " .. file .. ",maincpu,noloop") end
	if count == to then
		dbg:command("trace off,maincpu")
		manager.machine:exit()
	end
end)
