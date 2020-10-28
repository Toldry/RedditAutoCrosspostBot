:: this file is for running the bot on startup on my computer 
wsl tmux new-session -d -s racb
wsl tmux send-keys -t racb python3 Space /mnt/c/My/Projects/reddit_auto_crosspost_bot/reddit_auto_crosspost_bot.py Space --production Enter