How To Use:



Install the necessary python repos.



Open AMS2, run the launcher.py file. 



This will give you a bunch of steps:



Start a new project, select AMS2 in the dropdown (The focus has been AMS2, should work with Raceroom too, but haven’t played with it in months).



Press log race files. 



This will open a console and will begin logging events. This can be either in race or replay. However it doesn’t usually pick out the end of the session correctly, so press F12, when it’s the last lap, or the timing has run out.



When the race is finished, then click on Scan files (Top right)



This should populate the logger file into the control camera stage.



Press Run on control camera.

Open the replay of AMS2. You’ll then click on Generate. (This generates the JSON file for camera control). Wait a little bit.

When it’s done a pop up will pop up.

Say yes, to load it. 

Start OBS or whichever recording software you’d like to use.

Put in the time the replay shows the green flag. (Usually 5 seconds). Press Sync.

Now press play in the camera controller. This will automatically select the correct car.

Let it run to the end.

Stop the recording.

Close the camera controller.



Now scan again.

Say combine director and logs.



Scan again

Click create events for commentary.

Let it run for a moment. 

When it comes back close the console.



Scan again.

Select your video file in the space.

Click Review events

This opens your recorded video with the events below

Sync the green flag to the first event by clicking the event and say sync.

Then you can place manual events here if needed, or delete events.

At the end, save (You’ll need a new filename to not overwrite the existing one, I’ll fix this sometime).



Scan again.

Click add spice. (You should enter other information in the project notes, which will be used to create the talking points for banter, if not the AI will make up whatever it thinks)/

Let it run.



When finished, scan again

Then Click Calculate word count



Scan again.

Click generate commentary text. (Choose your commentator from the dropdown)



Scan again when it’s done.

Click Generate TTS audio.

This will open a browser to use Qwen 3TTS.

There are numerous settings here, but usually I make sure it’s using the 1.7Bil parameter version and scan the speaker files so it’s correct.

Also uncheck fast mode.

Let this run.



When it’s done (Usually on a 30 min race it’s about 15 mins. Depending on your GPU) scan again.



Then click combine audio



Now you’ll have your audio and video, take this into an editor and stick them on the timeline (They will  be synced up), and export. 



Broadcast Graphics (optional):

Scan again, then click Graphics Setup.

This opens a preview of your race video. Move the slider to a moment where the in-game leaderboard and track map are showing.

Layout tab: pick a region (leaderboard, map, logo, pop-ups, ticker) and drag a box over the video to place it. Leave Rows on 0 and the leaderboard frame grows/shrinks with the number of drivers.

Branding tab: championship name, season, round, logo (from Graphics/Logos) and colours. Add sponsor/manufacturer logos here too. Tick "Recolour logo white" for dark logos.

Drivers tab: links each name in the race log to a display name and a car preview image from Graphics/CarPreviews (matched automatically by name). "AI fill from project notes" reads your spotter guide for numbers, countries and liveries.

Leave "Also use as the default for future races" ticked and the next race starts with the same branding, layout and drivers (saved in Graphics/graphics_defaults.json).

Save & Close, scan again, then run Graphic Creator. The AI picks the moments for the pop-up graphics (driver cards, battles, overtakes, race control, pit stops, stats, positions gained, paddock news) and writes the text; every number on screen comes from the race data. Review them in Graphics Setup > Planned Graphics, and use "Render 30s test clip" to check a section.

Combine Video and Commentary then adds the graphics automatically. Untick "Add graphics to the final video" in Graphics Setup to export without them.
