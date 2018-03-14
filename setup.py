#!/usr/bin/env python3

import setuptools
import re

with open("teleredis/__init__.py", encoding="utf-8") as f:
    version = re.search(r"^__version__[\s\t=]*[\"']*([\w\d.\-_+]+)[\"']*$",
                        f.read(), re.M).group(1)

package_name = "teleredis"

setuptools.setup(
    name=package_name,
    packages=[package_name],
    version=version,

    url="https://github.com/ezdev128/telethon-session-redis",
    download_url="https://github.com/ezdev128/telethon-session-redis/releases",

    author="Konstantin M.",
    author_email="ezdev128@yandex.com",

    description="Redis backend for Telethon session storage",
    long_description=open("README.rst", encoding="utf-8").read(),


    license="MIT",

    classifiers=[
        #   3 - Alpha
        #   4 - Beta
        #   5 - Production/Stable
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
    ],
    keywords="telegram session sessions redis",
    python_requires="~=3.4",

    install_requires=[
        "redis>=2.0",
        "Telethon>=0.17"
    ],
)
