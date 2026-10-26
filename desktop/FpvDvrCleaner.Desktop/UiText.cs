namespace FpvDvrCleaner.Desktop;

/// <summary>
/// User interface text of the Repair and Export pages, the user's presets and the
/// OSD mask editor, in one place for the coming translation. XAML reads these with
/// {x:Static local:UiText.Name}; code formats the ones with {0} placeholders.
/// </summary>
public static class UiText
{
    // ---- signal and "How bad is it?"
    public const string SignalAnalog = "Analog";
    public const string SignalHdzero = "HDZero";
    public static string SignalName(string signal) => signal == "digital" ? SignalHdzero : SignalAnalog;

    public const string RepairEyebrow = "REPAIR";
    public const string HowBadLabel = "How bad is it?";
    public const string HowBadTip = "How damaged the recording is; each answer sets every repair at once. Fine-tune them under Advanced.";
    public const string LevelLight = "Light";
    public const string LevelNormal = "Normal";
    public const string LevelStrong = "Strong";
    public const string LevelAnalogLight = "A little grain and colour noise, every detail kept.";
    public const string LevelAnalogNormal = "Stripes and colour noise repaired, grain calmed.";
    public const string LevelAnalogStrong = "Heavy colour noise and rainbow tears, calmed strongly.";
    public const string LevelHdzeroLight = "Only the worst breakups rebuilt, every detail kept.";
    public const string LevelHdzeroNormal = "Breakups rebuilt, the picture calmed.";
    public const string LevelHdzeroStrong = "Frequent breakups and blocky noise.";
    public const string LevelNone = "Your own settings; pick an answer to start over from it.";
    public const string BestLabel = "Best quality";
    public const string BestDetail = "1080p, slower";
    public const string BestTip = "Upscales 1.5× (720p to 1080p) with detail from neighbouring frames and uses the Best speed setting. Takes the longest.";
    public const string StateLevel = "{0}";
    public const string StateCustom = "Custom (based on {0})";
    public const string StateCustomNoBase = "Custom settings";
    public const string StateUsing = "Using: {0}";

    // ---- my presets
    public const string MyPresetsEyebrow = "MY PRESETS";
    public const string MyPresetsEmpty = "Settings you use often can be kept here.";
    public const string SaveAsPreset = "+ Save current as preset…";
    public const string SaveAsPresetTip = "Keep the current settings, OSD choice and look as a preset of your own";
    public const string PresetNameWatermark = "Name of the preset";
    public const string Save = "Save";
    public const string Cancel = "Cancel";
    public const string PresetNameEmpty = "Give the preset a name.";
    public const string PresetNameTaken = "That name is taken. Choose another one.";
    public const string PresetSaved = "Saved \u201C{0}\u201D.";
    public const string PresetRenamed = "Renamed to \u201C{0}\u201D.";
    public const string PresetUpdated = "\u201C{0}\u201D now holds the current settings.";
    public const string PresetDeleted = "Deleted \u201C{0}\u201D.";
    public const string PresetDeleteQuestion = "Delete \u201C{0}\u201D?";
    public const string Delete = "Delete";
    public const string Keep = "Keep";
    public const string PresetMenuTip = "Rename, update or delete this preset";
    public const string PresetMenuRename = "Rename\u2026";
    public const string PresetMenuUpdate = "Update with current settings";
    public const string PresetMenuDelete = "Delete\u2026";
    public const string PresetRowTip = "{0}: {1}";

    // ---- repair page
    public const string CheckEyebrow = "CHECK THE RESULT";
    public const string PreviewDefaultTitle = "Frame at the trim playhead";
    public const string RenderFrame = "Render this frame";
    public const string PreviewHint = "Render a frame to compare original and cleaned picture.";
    public const string PreviewStatusIdle = "Renders a few frames around the playhead through every step. Drag the orange line to compare.";
    public const string PreviewStatusDone = "Drag the orange line in the picture to compare original and result.";
    public const string LabelOriginal = "ORIGINAL";
    public const string LabelCleaned = "CLEANED";
    public const string LabelWithout = "WITHOUT";
    public const string LabelWith = "WITH";
    public const string SizeLabel = "Output size";
    public const string SizeTip = "Original keeps the recorded size; 1.5× turns 720p into 1080p, using detail from neighbouring frames.";
    public const string SizeOriginal = "Original";
    public const string SpeedLabel = "Speed";
    public const string SpeedFast = "Fast";
    public const string SpeedBalanced = "Balanced";
    public const string SpeedBest = "Best";
    public const string SpeedNoteFast = "Quickest export, fine for checking a flight.";
    public const string SpeedNoteBalanced = "Recommended: nearly Best, in clearly less time.";
    public const string SpeedNoteBest = "Finest motion and most neighbours, for the final cut.";
    public const string OsdLabel = "OSD";
    public const string OsdTip = "The on-screen display: text, icons and bars drawn over the picture.";
    public const string OsdKeep = "Keep";
    public const string OsdRemove = "Remove";
    public const string OsdKeepNote = "Text, icons and bars stay in the video.";
    public const string OsdRemoveNote = "Found automatically and refilled from frames around it.";
    public const string OsdRemoveHandNote = "Found automatically, with your corrections.";
    public const string OsdFixByHand = "Fix by hand…";
    public const string OsdFixByHandTip = "Add what the automatic search missed, such as small icons or REC, or keep parts it took by mistake.";
    public const string OsdSearchLabel = "Find the OSD";
    public const string OsdSearchTech = "mark what the search finds";
    public const string AutoRendering = "Updating the frame…";
    public const string OsdShowTip = "Mark what would be removed on the frame at the playhead";
    public const string LookLabel = "Look";
    public const string LookOff = "Off: the picture stays exactly as repaired.";
    public const string LookPresetOn = "{0}, applied after every repair.";
    public const string LookCustomOn = "Custom look, applied after every repair.";
    public const string Adjust = "Adjust";
    public const string Hide = "Hide";
    public const string AdvancedHeader = "Advanced";
    public const string AdvancedNote = "Every repair on its own. Show renders this frame without and with just that one effect.";
    public const string SignalLabel = "Signal";
    public const string SignalNoteAnalog = "Rainbow stripes, colour dropouts and colour noise are rebuilt from neighbouring frames.";
    public const string SignalNoteHdzero = "Breakups and broken blocks are rebuilt from the clean frames around them.";
    public const string ColourLabel = "Fix colours";
    public const string ColourTech = "colour repair";
    public const string ColourExplain = "Fix colours rebuilds rainbow stripes, colour dropouts and colour noise from neighbouring frames; brightness is never touched.";
    public const string BlockLabel = "Remove HDZero breakups";
    public const string BlockTech = "block repair";
    public const string BlockExplain = "Remove HDZero breakups rebuilds blocks lost to coloured confetti from the clean frames before and after.";
    public const string NoiseLabel = "Calm the picture";
    public const string NoiseTech = "noise reduction";
    public const string NoiseExplain = "Calm the picture fuses five aligned frames: grain and streaks disappear, moving edges stay sharp.";
    public const string DetailLabel = "Sharpness";
    public const string DetailTech = "detail";
    public const string DetailExplain = "Sharpness adds a mild, clipped lift to edges, without halos.";
    public const string MotionLabel = "Motion smoothing";
    public const string MotionTech = "motion blend";
    public const string MotionExplain = "Motion smoothing blends each frame a little with the one before while encoding; a still frame cannot show it.";
    public const string EngineLabel = "Engine";
    public const string EngineStandard = "Standard (no model needed)";
    public const string EngineModel = "Standard + my trained model";
    public const string ModelWatermark = "Select a trained model";
    public const string ModelFile = "Model file";
    public const string Browse = "Browse";
    public const string ShowButton = "Show";
    public const string ShowTip = "Render this frame without and with only this effect";
    public const string ShowTitle = "{0}: without and with";
    public const string ShowRendering = "Rendering the frame without and with {0}…";
    public const string ShowOff = "{0} is at 0. Raise it, then press Show to see what it does.";
    public const string ZeroTurnsOff = "0 turns a repair off.";

    // ---- OSD mask editor
    public const string MaskEyebrow = "OSD MASK";
    public const string MaskTitle = "Fix the OSD mask by hand";
    public const string MaskLoading = "Looking for the OSD";
    public const string MaskLoadingText = "Scanning {0} for text, icons and bars that hold still.";
    public const string MaskBrushAdd = "Brush: remove";
    public const string MaskBrushAddTip = "Paint over what should be removed too (A)";
    public const string MaskEraser = "Eraser: keep";
    public const string MaskEraserTip = "Paint over what must stay in the picture (E)";
    public const string MaskRectAdd = "Box: remove";
    public const string MaskRectAddTip = "Drag a box around what should be removed (R)";
    public const string MaskRectKeep = "Box: keep";
    public const string MaskRectKeepTip = "Drag a box around what must stay (K)";
    public const string MaskSize = "Brush size";
    public const string Undo = "Undo";
    public const string Redo = "Redo";
    public const string MaskReset = "Reset to automatic";
    public const string MaskResetTip = "Forget every correction and use the automatic search alone";
    public const string Done = "Done";
    public const string MaskLegendRemoved = "Removed";
    public const string MaskLegendKept = "Kept by hand";
    public const string MaskHint = "Orange is painted out and refilled in every frame of this clip. Ctrl+Z / Ctrl+Y undo and redo; the mouse wheel changes the brush size.";
    public const string MaskStats = "Automatic: {0:0.0} % of the picture · with your corrections: {1:0.0} %";
    public const string MaskSaved = "Your OSD corrections for {0} are saved and used in preview and export.";
    public const string MaskCleared = "Back to the automatic OSD search for {0}.";
    public const string MaskFailed = "The OSD search failed: {0}";
    public const string NeedClip = "Add and select a clip first.";

    // ---- export page
    public const string SaveToEyebrow = "SAVE TO";
    public const string ChangeFolder = "Change…";
    public const string OpenFolder = "Open folder";
    public const string FolderCreatedOnExport = "created when you export";
    public const string FolderNone = "Choose a folder for the cleaned files";
    public const string PurposeEyebrow = "WHAT IS THE VIDEO FOR?";
    public const string PurposeShareTitle = "Share & play anywhere";
    public const string PurposeShareLine = "Phones, TVs, YouTube and every editor";
    public const string PurposeSmallTitle = "Smallest file";
    public const string PurposeSmallLine = "Half the size; newer players only";
    public const string PurposeEditTitle = "For video editing";
    public const string PurposeEditLine = "Every detail kept; very large files";
    public const string PurposeWebTitle = "For the web";
    public const string PurposeWebLine = "Websites and browsers; slow to encode";
    public const string PurposeOther = "Chosen under More options: {0}.";
    public const string MoreOptions = "More options";
    public const string FileFormat = "FILE FORMAT";
    public const string QualityLabel = "QUALITY (14 BEST – 32 SMALLEST)";
    public const string SizeLimitLabel = "SIZE LIMIT, MB (0 = NONE)";
    public const string CaptionLabel = "CAPTION BURNED INTO THE VIDEO";
    public const string CaptionWatermark = "e.g. pilot name or spot";
    public const string SuffixLabel = "FILE NAME SUFFIX";
    public const string JoinClips = "Join all clips into one video";
    public const string OpenWhenDone = "Open the folder when done";
    public const string ExportOne = "Export";
    public const string ExportMany = "Export {0} clips";
    public const string ExportCancel = "Cancel";
    public const string ExportSummaryEmpty = "The queue is empty. Add clips in Import first.";
    public const string ExportSummary = "{0} clip(s), {1} of video · {2}";
    public const string ShowSettings = "Show settings";
    public const string HideSettings = "Hide settings";
    public const string ExportFoldedSummary = "{0} · {1}";
    public const string Estimating = "Estimating remaining time…";
    public const string ExportStarting = "Starting…";
}
