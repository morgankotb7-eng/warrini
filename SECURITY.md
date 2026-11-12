# Security

Warrini runs on your own machine and is built so that nothing leaves it unless a guide explicitly
allows the online voice.

## Reporting a problem

Please open a private security advisory on GitHub
(Security → Advisories → Report a vulnerability) instead of a public issue. Include the steps to
reproduce and the version (`warrini --version`). I aim to reply within a week.

## What is in scope

- The local web server (`warrini` / `warrini ui`): authentication, cross-site requests, path handling,
  uploads.
- Redaction: any case where a covered area shows original pixels in a rendered video.
- Anything that sends data off the machine without the guide's online-voice setting.
