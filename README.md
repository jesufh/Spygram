# Spygram
<div align="center">
<img src="https://github.com/user-attachments/assets/c4bd3e5e-911a-4d54-ada3-40fa363b45d0" /><br>
<b>Download instagram content fasty.</b>
</div><br>
Spygram is a modular, asynchronous tool that downloads posts, stories, highlights, reels, filters by tags users, and more—exclusively from public profiles.

## Installation
Requires Python 3.10+. Install the dependencies, then you can run the module.
```python
# requirements.txt
pip install -r requirements.txt
```

## Documentation
It uses simple arguments; you can view them with `--help`. You must be logged into Instagram; you can extract the cookies directly from your preferred browser or by pasting them into the program.
```
.\spygram <--user “target”>
          [--browser-cookies]
          [--session "name_session"]
          [--session-id "id_session"]
          [--all, --profile, --posts, --stories, --reels, --highlights, --tagged, --saved]
          [--limit "number"]
```

For example:
```bash
# Download all posts
py -m spygram --session "iscami" --user "dualipa" --posts

# Download all stories
py -m spygram --session "iscami" --user "dualipa" --stories

# Extract cookies from the browser
# automatically detects cookies in installed browsers
py -m spygram --user "iscami" --browser-cookies
```
