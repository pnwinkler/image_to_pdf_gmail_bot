This is a personal project. This means you can use it as you like, but please note that I expect nothing of you, and promise nothing to you.

# What this repo contains
WIP: this is a quick & dirty script for a GMail bot that returns images to sender, as PDFs. It uses a sender whitelist when determining who to respond to, and trashes old emails (NOT YET IMPLEMENTED). 

## Worth noting
If you're not afraid of the shell, you're probably better off ignoring this repo, and instead having ImageMagick installed, then using some Bash, roughly like this:
```
for img in *.{png,jpg,jpeg}; do
    convert "$img" -auto-orient "./as_pdf/${img%.*}.pdf"
done
```
