# Held-out split

The YAML files in this folder are **never published** (`.gitignore` excludes them). Their hashes
are in `../cases-private.sha256`, so anyone can later verify the split did not change.

Run them like any other cases, and compare with the public split to spot contamination:

```bash
python3 -m qbench run --model <name> --cases cases-private --k 5 --run-id <id>-private
```

A model that scores much higher on the public split than on these is suspect: it may have
seen the public cases during training.
