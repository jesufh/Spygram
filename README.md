# Spygram
<div align="center">
<img src="https://github.com/user-attachments/assets/c4bd3e5e-911a-4d54-ada3-40fa363b45d0" /><br>
<b>Download instagram content fasty.</b>
</div><br>
Spygram is a modular, asynchronous tool that downloads posts, stories, highlights, reels, filters by tags users, and more from public and private profiles.

## Installation
Requires Python 3.10+. First, clone the repository and navigate to the project root.

### Standard Installation
Install the required dependencies using:
```bash
pip install -r requirements.txt
```

### Development Installation
To install the module in editable mode for development:
```bash
pip install -e .
```

## Documentation
It uses simple arguments; you can view them with `--help`. You must be logged into Instagram to use cookies directly from your preferred browser, or simply use anonymous mode.
```
.\spygram <--user “target”>
          [--browser-cookies "firefox, chrome, opera, ..."]
          [--session "name_session"]
          [--all, --profile, --posts, --stories, --reels, --highlights, --tagged]
          [--limit "number"]
          [--since "YYYY-MM-DD"]
          [--proxy "url"]
          [--output-dir "path"]
          [--max-concurrent "number"]
          [--clear-cache]
```

For example:
```bash
# Download all posts
py -m spygram --session "iscami" --user "dualipa" --posts

# Download all stories
py -m spygram --session "iscami" --user "dualipa" --stories

# Extract cookies from the browser
# automatically detects cookies in installed browsers
py -m spygram --user "iscami" --browser-cookies firefox
```