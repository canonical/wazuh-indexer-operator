# Changelog

All notable changes to this project will be documented in this file. This will include only change that differ from the [upstream OpenSearch charm](https://github.com/canonical/opensearch-operator).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each revision is versioned by the date of the revision.

## 2026-06-15

### Fixed
- Pin setuptools <82 for `jproperties` to fix charmcraft pack. Also pin
  `types-psutil`/`types-setuptools` to the newest releases that still build
  with setuptools <82 (their build dependencies of `mypy`, pulled when
  `charset-normalizer` builds from source, otherwise require setuptools >=82
  and conflict with the pin above).

## 2025-08-22

### Updated

- Removed old documentation workflow in favor of an updated workflow to inject a custom word list and check links.

## 2025-07-21

### Added

- Changelog added for tracking changes.
