local frames, fontLoads, messages = {}, 0, {}
local failFont = false
local Frame = {}
Frame.__index = Frame

function Frame:SetPoint(point, _, _, x, y)
	self.points[point] = {x, y}
end
function Frame:ClearAllPoints() self.points = {} end
function Frame:SetSize(width, height) self.width, self.height = width, height end
function Frame:SetWidth(width) self.width = width end
function Frame:GetWidth() return self.width or 1900 end
function Frame:GetEffectiveScale() return UIParent.scale or 1 end
function Frame:GetHeight()
	if self.height then return self.height end
	return self.parent:GetHeight() + self.points.TOPLEFT[2] - self.points.BOTTOMRIGHT[2]
end
function Frame:SetScript(name, callback) self.scripts[name] = callback end
function Frame:SetText(text) self.text = text end
function Frame:SetFont(path, size)
	assert(path == "Interface\\AddOns\\DBM-Core\\fonts\\DBMBinary.ttf")
	fontLoads = fontLoads + 1
	if failFont then return false end
	self.binary = true
	self.fontSize = size
	return true
end
function Frame:SetFontObject() self.binary = false end
function Frame:Show() self.shown = true end
function Frame:Hide() self.shown = false end
function Frame:IsShown() return self.shown end
function Frame:CreateFontString()
	local frame = setmetatable({parent = self, points = {}, scripts = {}, fontString = true}, Frame)
	frames[#frames + 1] = frame
	return frame
end
for _, method in ipairs({
	"SetFrameStrata", "ApplyBackdrop", "SetBackdropColor", "SetClampedToScreen",
	"SetMovable", "SetToplevel", "EnableMouse", "RegisterForDrag", "SetJustifyH",
	"SetJustifyV", "SetWordWrap", "SetMaxLines", "SetClipsChildren", "EnableMouseWheel",
	"SetTextColor",
}) do
	Frame[method] = function() end
end

UIParent = setmetatable({width = 1920, height = 1080}, Frame)
GameFontHighlightSmall = {}
DBM_CORE_L = {
	DEBUG_LOG_BINARY = "Binary capture font",
	DEBUG_LOG_TEXT = "Human-readable font",
	DEBUG_LOG_FONT_ERROR = "Font failed",
}
function CreateFrame(_, _, parent)
	local frame = setmetatable({parent = parent, points = {}, scripts = {}}, Frame)
	frames[#frames + 1] = frame
	return frame
end
function GetTime() return 100 end
function InCombatLockdown() return true end
local DBM = {Options = {DebugMode = false, DebugLevel = 1, DebugLogBinaryFont = false}}
function DBM:FireEvent() end
function DBM:AddMsg(text) messages[#messages + 1] = text end
local module = {}
local private = {isRetail = true}
function private:NewModule() return module end
function private:GetPrototype() return DBM end
function module:RegisterShortTermEvents() end
function module:UnregisterShortTermEvents() end
assert(loadfile("DBM-Retail/DBM-Core/modules/DevTools.lua"))("DBM-Core", private)
module:OnModuleLoad()
assert(#frames == 0 and fontLoads == 0, "Disabled debug creates no capture frames/fonts")

DBM.Options.DebugMode = true
module:OnDebugToggle()
DBM:ToggleDebugLog()
for i = 1, 120 do
	DBM:Debug("record " .. i, 4, nil, nil, true, true)
end
assert(fontLoads == 0, "Never-enabled binary font must not load")
local function button(text)
	for _, frame in ipairs(frames) do
		if not frame.fontString and frame.text == text then return frame end
	end
	error("Missing button " .. text)
end
local function click(text) button(text).scripts.OnClick() end
local function visibleRecords()
	local records = {}
	for _, frame in ipairs(frames) do
		if frame.fontString and frame.shown and frame.text and
			(frame.text:find("^record ") or frame.text:find("^~DBM1~record ")) then
			records[#records + 1] = frame.text
		end
	end
	return records
end
local textRows = #visibleRecords()
click("Binary capture font")
assert(DBM.Options.DebugLogBinaryFont and fontLoads > 0)
local records = visibleRecords()
assert(#records > 0 and #records < textRows, "Header reserves room without clipping")
for _, record in ipairs(records) do
	assert(record:match("^~DBM1~record %d+~END~$"), "Native framing preserves raw text")
end
local header
for _, frame in ipairs(frames) do
	if frame.fontString and frame.text == ("~DBM1~#DBMP1:%d~END~"):format(#records) then header = frame end
	assert(not frame.scripts.OnUpdate, "Font adds no polling")
end
assert(header and header.shown)
local first = tonumber(records[1]:match("record (%d+)"))
click("vv")
assert(tonumber(visibleRecords()[1]:match("record (%d+)")) == first, "Already at bottom")
click("^^")
local previous = visibleRecords()
assert(tonumber(previous[1]:match("record (%d+)")) == first - #records, "Pages have no gaps")
click("Human-readable font")
assert(not DBM.Options.DebugLogBinaryFont and not header.shown)
for _, record in ipairs(visibleRecords()) do assert(record:match("^record %d+$")) end

failFont = true
click("Binary capture font")
assert(not DBM.Options.DebugLogBinaryFont and messages[#messages] == "Font failed")
failFont = false
click("Binary capture font")
DBM.Options.DebugLogBinaryFont = false
DBM:Debug("profile reset record", 4, nil, nil, true, true)
assert(not header.shown, "Profile changes restore the font on refresh")
click("Binary capture font")
click("Clear")
assert(header.text == "~DBM1~#DBMP1:0~END~")
UIParent.scale = 0.7
DBM:Debug("record 999", 4, nil, nil, true, true)
assert(math.abs(header.fontSize * UIParent.scale - 12) < 0.001, "Binary cells retain physical pixel size")
assert(header.text == "~DBM1~#DBMP1:1~END~")
DBM.Options.DebugMode = false
module:OnDebugToggle()
print("Binary debug viewer: default/off, toggle, framing, pagination, clear, font failure, profile reset, UI scale passed")
