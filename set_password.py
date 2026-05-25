#!/usr/bin/env python3
"""
Run once to set (or change) your login password:
  python set_password.py
"""
import getpass, os, yaml

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')

def main():
    from auth import hash_password
    pw  = getpass.getpass('New password: ')
    pw2 = getpass.getpass('Confirm password: ')
    if pw != pw2:
        print('Passwords do not match.')
        return
    if len(pw) < 8:
        print('Password must be at least 8 characters.')
        return

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}

    cfg['password_hash'] = hash_password(pw)

    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False)

    print('Password set. You can now log in.')

if __name__ == '__main__':
    main()
