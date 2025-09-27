#!/bin/bash

# Change to your ssh key
SSH_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIFcJCEv4bHApgfmbuEb9MOlEMsusfke3CQUjXuKGgn5 my-laptop"

# not working properly
if grep -q "$SSH_KEY" ~/.ssh/authorized_keys; then
    echo "SSH key already exists in authorized_keys"
    exit 0
fi

echo "$SSH_KEY" >> ~/.ssh/authorized_keys

if ! grep -q "$SSH_KEY" ~/.ssh/authorized_keys; then
    echo "SSH key not found in authorized_keys"
    exit 1
fi

echo "SSH key added to authorized_keys"
exit 0