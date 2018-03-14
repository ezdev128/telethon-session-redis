import setuptools

setuptools.setup(
    name="telethon-session-redis",
    version="0.1.0",
    url="https://github.com/ezdev128/telethon-session-redis",
    download_url="https://github.com/ezdev128/telethon-session-redis/releases",

    author="Konstantin M.",
    author_email="ezdev128@yandex.com",

    description="Redis backend for Telethon session storage",
    long_description=open("README.rst", encoding="utf-8").read(),

    packages=setuptools.find_packages(),
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
