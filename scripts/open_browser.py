"""Wait for the local test application before opening its sign-in page."""
import time
import urllib.error
import urllib.request
import webbrowser


def main():
    # These Windows testing launchers deliberately target only loopback.
    url = 'http://127.0.0.1:8000/login/'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200 and b'Welcome back' in response.read():
                    if not webbrowser.open(url):
                        print('Open your browser and enter http://127.0.0.1:8000')
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.5)
    print('The application did not become ready. Check the clinic console for errors.')


if __name__ == '__main__':
    main()
