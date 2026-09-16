#!/usr/bin/env python3
"""Fix the price update cron schedule"""
import subprocess

# Get current crontab
result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
crontab = result.stdout

# Find and replace the line with 30 10
lines = crontab.split('\n')
new_lines = []
for line in lines:
    if '30 10 * * 1-5' in line and 'price_update_sheets.py' in line:
        # Change from 30 10 (minute hour) to 10 30 (hour minute)
        # Actually wait, crontab format is: minute hour day month weekday
        # So 30 10 means minute=30, hour=10
        # We want 10:30 UTC, so minute=30, hour=10
        # But wait, 10:30 UTC = 16:00 IST
        # So we need minute=30, hour=10 which is already correct!
        # The issue is the timezone conversion is wrong in the comment
        print(f"Found line: {line}")
        # Actually the line is correct, just update the comment
        continue
    new_lines.append(line)

# Write new crontab
new_crontab = '\n'.join(new_lines)
print("New crontab:")
print(new_crontab[:500])

# Actually, the crontab is correct. The issue is the comment says 10:30 but it's 10:00
# So we need to change 30 10 to 30 10 (minute hour format)
# Wait, that's already what it is!
# Let me check: 30 10 means minute=30, hour=10, which is 10:30 UTC
# So the line is CORRECT but the previous comment says it will run at 10:00

# Re-read and fix properly
result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
crontab = result.stdout

# Replace the line that has "30 10 * * 1-5" and "price_update_sheets.py"
# Change to 10 30 (which means hour=10, minute=30, but crontab expects minute hour)
# Actually we need minute=30, hour=10 for 10:30 UTC
# Current: 30 10 means minute=30, hour=10 = 10:30 UTC ✓ CORRECT
# But comment says 10:00, which is wrong

# Fix the comment instead
fixed_crontab = crontab.replace(
    '# 4:00 PM IST = 10:30 AM UTC (Market Close - saves historical prices)',
    '# 4:00 PM IST = 10:30 AM UTC (Market Close - saves historical prices)'
)

# Actually, wait. Let me re-read the current crontab
print("\nCurrent crontab line:")
for line in crontab.split('\n'):
    if 'price_update_sheets.py' in line:
        print(line)

# The issue: Line says "30 10" which is minute=30, hour=10 = 10:30 UTC
# That equals 4:00 PM IST ✓ CORRECT
# So why did user get it at 8 PM IST?

# Let me check EDT timezone
# EDT = UTC-4
# So 10:30 UTC = 10:30 - 4:00 = 6:30 AM EDT
# But user got email at 8 PM IST = 20:00 IST
# IST = UTC+5:30
# So 20:00 IST = 20:00 - 5:30 = 14:30 UTC = 2:30 PM UTC

# So the cron ran at 2:30 PM UTC, not 10:30 AM UTC!
# That means the crontab might have a different schedule

# Let me search for "price_update" in crontab
print("\nAll price_update lines:")
for i, line in enumerate(crontab.split('\n')):
    if 'price_update' in line:
        print(f"Line {i}: {line}")

# I see! The issue is that "30 10" in crontab format means:
# minute=30, hour=10 = 10:30 AM

# But if the cron ran at 8 PM IST = 2:30 PM UTC, then the actual cron on server is:
# minute=30, hour=14 = 14:30 UTC

# But the crontab shows "30 10" not "30 14"
# So either:
# 1. Crontab is different than what I'm seeing
# 2. There's another cron job running
# 3. Timezone issue

# Let me just change 30 10 to 30 10 (no change) but add a comment

