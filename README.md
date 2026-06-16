# Discord Music Bot

<div align="center">
  <img src="./assets/bot-avatar.png" width="90" alt="Discord Bot Avatar"/>

  <h3>Python Discord bot with YouTube music playback</h3>
  <p>
    Built with <a href="https://discordpy.readthedocs.io/">discord.py</a>,
    <a href="https://github.com/yt-dlp/yt-dlp">yt-dlp</a>, and FFmpeg.
  </p>

  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/discord.py-voice-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="discord.py voice"/>
  <img src="https://img.shields.io/badge/yt--dlp-YouTube-red?style=for-the-badge" alt="yt-dlp"/>
  <img src="https://img.shields.io/badge/FFmpeg-required-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg required"/>
</div>

---

## Overview

Discord bot viet bang Python, tap trung vao slash command, prefix command va phat nhac YouTube trong voice channel. Project duoc tach theo `cogs`, `services`, `utils` de de bao tri va mo rong.

## Features

### Music Player

- Phat nhac tu YouTube link hoac tu khoa tim kiem.
- Queue rieng cho tung Discord server.
- Tu dong phat bai tiep theo khi bai hien tai ket thuc.
- Ho tro `/pause`, `/resume`, `/skip`, `/stop`, `/leave`.
- Dung `yt-dlp` de lay audio stream va FFmpeg de phat vao voice channel.
- Uu tien audio Opus va refresh stream truoc khi phat de giam loi bi im/giat.

### General Commands

- `!ping` de kiem tra bot con phan hoi khong.
- `/hello` de bot chao nguoi dung.
- `/say <text>` de bot gui lai noi dung duoc nhap.

### Admin Commands

- `/clear <amount>` de xoa tin nhan trong channel.
- Ho tro nhap so luong hoac `all`.
- Kiem tra quyen `Manage Messages` truoc khi xoa.

## Commands

| Command | Description |
| --- | --- |
| `!ping` | Kiem tra bot con phan hoi khong |
| `/hello` | Bot chao nguoi dung |
| `/say <text>` | Bot gui lai noi dung duoc nhap |
| `/clear <amount>` | Xoa tin nhan, nhap so hoac `all` |
| `/play <query>` | Phat nhac tu link YouTube hoac tu khoa |
| `/queue` | Xem bai dang phat va danh sach cho |
| `/pause` | Tam dung bai dang phat |
| `/resume` | Phat tiep bai dang pause |
| `/skip` | Bo qua bai hien tai |
| `/stop` | Dung nhac va xoa queue |
| `/leave` | Cho bot roi voice channel |

---

## Project Structure

```txt
discord-bot/
|-- main.py
|-- requirements.txt
|-- README.md
|-- assets/
|   `-- bot-avatar.png
`-- bot/
    |-- client.py
    |-- config.py
    |-- cogs/
    |   |-- admin.py
    |   |-- general.py
    |   `-- music.py
    |-- services/
    |   |-- logger.py
    |   `-- youtube.py
    `-- utils/
        `-- checks.py
```

## Installation

### 1. Create Discord Application

1. Vao [Discord Developer Portal](https://discord.com/developers/applications).
2. Tao application va tao bot.
3. Copy bot token de cau hinh vao `.env`.
4. Bat **Message Content Intent** neu muon dung prefix command nhu `!ping`.
5. Moi bot vao server voi cac quyen can thiet:
   - View Channel
   - Send Messages
   - Use Application Commands
   - Connect
   - Speak
   - Use Voice Activity
   - Manage Messages, neu dung `/clear`

### 2. Create Virtual Environment

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

### 3. Install Dependencies

```powershell
pip install -r requirements.txt
```

Packages hien tai:

```txt
discord.py[voice]
python-dotenv
yt-dlp
```

### 4. Install FFmpeg

Windows:

```powershell
winget install Gyan.FFmpeg
```

Kiem tra:

```powershell
ffmpeg -version
```

### 5. Configure Environment

Tao file `.env` o thu muc goc:

```env
DISCORD_TOKEN=your_bot_token_here
COMMAND_PREFIX=!
```

`DISCORD_TOKEN` la bat buoc. `COMMAND_PREFIX` khong bat buoc, mac dinh la `!`.

## Start Bot

```powershell
.\.venv\Scripts\python.exe main.py
```

Hoac khi da activate `.venv`:

```powershell
python main.py
```

Khi chay thanh cong, console se in ra cog da load, so slash command da sync va tai khoan bot online.

---

## Quick Music Test

1. Vao mot voice channel.
2. Chay lenh:

```txt
/play never gonna give you up
```

Hoac dung YouTube URL:

```txt
/play https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

Neu bot vao voice nhung khong nghe tieng hoac bi giat:

- Kiem tra `ffmpeg -version`.
- Cap nhat `yt-dlp` bang `pip install -U yt-dlp`.
- Kiem tra quyen Connect va Speak cua bot.
- Kiem tra mang cua may/VPS dang chay bot.

## Notes

- Queue dang luu trong RAM, restart bot se mat queue.
- Bot chua ho tro playlist, Spotify, volume control, database hoac web dashboard.
- Logic YouTube nam trong `bot/services/youtube.py`.
- Logic voice va queue nam trong `bot/cogs/music.py`.

## Roadmap

- Them `/nowplaying`.
- Them volume control.
- Them Discord UI button cho music player.
- Luu queue hoac lich su bai hat vao database.
- Them role DJ de gioi han quyen skip/stop.
