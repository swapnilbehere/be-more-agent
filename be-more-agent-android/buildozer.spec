[app]

title = Be More Agent
package.name = bemoreagent
package.domain = com.brenpoly
source.dir = .
source.include_exts = py,png,jpg,kv,json,wav
version = 1.0.0

requirements = python3,kivy==2.3.0,pillow,pyjnius,oscpy,duckduckgo-search,numpy,llamacpp,typing-extensions,diskcache,jinja2,markupsafe

android.api = 33
android.minapi = 28
android.ndk_api = 28
android.archs = arm64-v8a

android.permissions = INTERNET,RECORD_AUDIO,CAMERA,FOREGROUND_SERVICE,WAKE_LOCK

services = WakewordService:services/wakeword_service.py:foreground:sticky

source.include_patterns = services/*.py,lib/*.py,assets/**/*,ui/*.kv

orientation = portrait
fullscreen = 1

android.accept_sdk_license = True

# Custom recipe for llama.cpp cross-compilation
p4a.local_recipes = ./recipes

[buildozer]
log_level = 2
warn_on_root = 1
