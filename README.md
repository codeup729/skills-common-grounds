# skills-common-grounds

A collection of Claude Code skills.

## Skills

- **AppleApplySkill/**: `apple-ios-apply` finds iOS / iOS-frameworks roles on jobs.apple.com posted in the last 7 days that need ≤ 2 years of experience (US only). It applies with your existing Apple Careers profile through Claude in Chrome and asks for confirmation before each submit.
- **iOSJobsApplySkill/**: `ios-jobs-apply` does the same across ~25 top US iOS employers (Airbnb, Snap, Notion, Reddit, Amazon, Netflix, Stripe, Coinbase, Google, Meta, Microsoft, Uber, …). It covers roles posted in the last 7 days that need ≤ 3 years of experience.
  - `find_jobs.py` searches the public job feeds (Greenhouse, Ashby, Lever, Workday, Amazon, Netflix). The browser covers the rest.
  - Forms are filled from `profile.json`, and the resume comes from `resume/`.
  - You sign in to portals yourself and approve every submit.

Open Claude Code inside a skill's folder and run `/apple-ios-apply` or `/ios-jobs-apply`.
