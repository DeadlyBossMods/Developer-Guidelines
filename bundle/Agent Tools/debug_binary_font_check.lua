local frames, fontLoads, messages = {}, 0, {}
local failFont = false
local registeredFonts, fontObjects = {}, 0
local windows = arg[1] == "--windows" or arg[2] == "--windows"
local binaryPath = "Interface\\AddOns\\DBM-Core\\fonts\\DBMBinary.ttf"
local resolvedBinaryPath = windows and "resolved-binary-font-asset" or binaryPath
local setFontAfterText = 0
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
function Frame:SetText(text)
	self.text = text
	self.textAssigned = true
end
function Frame:SetFont(path, size)
	if path == "Fonts\\FRIZQT__.TTF" then
		self.binary = false
		self.fontSize = size
		self.explicitFont = true
		return true
	end
	assert(path == binaryPath or path == resolvedBinaryPath)
	fontLoads = fontLoads + 1
	if self.textAssigned then
		setFontAfterText = setFontAfterText + 1
		if windows then return false end
	end
	if failFont or not registeredFonts[path] or not self.fontObject then return false end
	self.binary = true
	self.fontSize = size
	self.explicitFont = true
	return true
end
function Frame:SetFontObject(font)
	self.fontObject = font
	if not self.explicitFont then
		self.binary = font and font.fontSize ~= nil
	end
end
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
GameFontHighlightSmall = {
	GetFont = function() return "Fonts\\FRIZQT__.TTF", 10, "" end,
}
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
function CreateFont(name)
	assert(name == "DBMDebugLogBinaryFont")
	fontObjects = fontObjects + 1
	assert(fontObjects == 1, "Reuse the binary font object across toggles and scale changes")
	return {
		SetFont = function(self, path, size)
			assert(path == binaryPath)
			registeredFonts[resolvedBinaryPath] = true
			self.fontSize = size
			for _, frame in ipairs(frames) do
				if frame.fontObject == self then frame.fontSize = size end
			end
		end,
		GetFont = function(self)
			return resolvedBinaryPath, self.fontSize, ""
		end,
	}
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
assert(#frames == 0 and fontLoads == 0 and fontObjects == 0, "Disabled debug creates no capture frames/fonts")

DBM.Options.DebugMode = true
if arg[1] == "--saved" then
	DBM.Options.DebugLogBinaryFont = true
	module:OnDebugToggle()
	assert(fontObjects == 1 and fontLoads > 0, "Saved binary selection registers before creating rows")
	print("Saved binary font initialization passed")
	return
end
module:OnDebugToggle()
DBM:ToggleDebugLog()
for i = 1, 120 do
	DBM:Debug("record " .. i, 4, nil, nil, true, true)
end
assert(fontLoads == 0 and fontObjects == 0, "Never-enabled binary font must not load")
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
local debugFrame = frames[1]
local originalWidth, originalHeight = debugFrame.width, debugFrame.height
failFont = true
click("Binary capture font")
assert(not DBM.Options.DebugLogBinaryFont and messages[#messages] == "Font failed")
failFont = false
click("Binary capture font")
assert(DBM.Options.DebugLogBinaryFont and fontLoads > 0 and fontObjects == 1)
assert(debugFrame.width == originalWidth and debugFrame.height == originalHeight, "Font toggle preserves window size")
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
for _, frame in ipairs(frames) do
	if frame.fontString and frame.shown and frame.text and frame.text:find("^record ") then
		assert(not frame.binary and frame.fontSize == 10, "Restore explicit human font overrides")
	end
end
assert(debugFrame.width == originalWidth and debugFrame.height == originalHeight, "Restoring text preserves window size")

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
assert(setFontAfterText == 0, "Never change a FontString font after text is assigned")
DBM.Options.DebugMode = false
module:OnDebugToggle()
print("Binary debug viewer: default/off, toggle, framing, pagination, clear, font failure, profile reset, UI scale passed")
