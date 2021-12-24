from ._base import BasicView
import getpass
import sys

from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from ..session import Session


class BasicCLIView(BasicView):
    def __init__(self):
        pass

    @classmethod
    def _get_priority(cls):
        return 0

    def display_error(self, message: str) -> None:
        print("Error: ", message, file=sys.stderr)

    def display_notice(self, message: str) -> None:
        print(message)

    def _session_to_string(self, s: "Session", default_session: "Session") -> str:
        flags = []
        if s == default_session:
            flags.append('default')
        if s.environment.name != 'prod':
            flags.append(f'env:{s.environment.name}')
        
        if len(flags) > 0:
            flags = f" [{', '.join(flags)}]"
        else:
            flags = ''
        return f'{s.AccountName}{flags}'

    def display_session_list(self, sessions : list["Session"], ask_to_select_one : bool = False) -> None:
        if len(sessions) == 0:
            print("No active sessions")
        else:
            print(f"Active session list [{len(sessions)}]:")
            print('')
            sorted_sessions = list(sorted(sessions, key=lambda x: x.AccountName))
            for session_id, s in enumerate(sorted_sessions):
                if ask_to_select_one:
                    print(f' [{session_id+1:2d}] {self._session_to_string(s, sessions[0])}')
                else:
                    print(f"- {self._session_to_string(s, sessions[0])}")

            if ask_to_select_one:
                while True:
                    user_input = input("Please select a session: ")
                    if user_input.isnumeric():
                        user_input = int(user_input)
                        if user_input >= 1 and user_input <= len(sorted_sessions):
                            return sorted_sessions[user_input - 1]
                        else: 
                            print("Invalid input!")
                    else:
                        for s in sorted_sessions:
                            if s.AccountName == user_input:
                                return s
                        print("Invalid input!")

    def ask_credentials(self, ask_login: bool = False, ask_password: bool = False, ask_2fa: bool = False) -> tuple[Optional[str], Optional[str], Optional[str]]:
        login = None
        password = None
        twofa = None
        if ask_login:
            login = input("Please enter your user name: ")
            if login == '':
                login = None
        if ask_password:
            password = getpass.getpass()
            if password == '':
                password = None
        if ask_2fa:
            twofa = input("Please enter your 2FA code: ")
            if twofa == '' or not twofa.isnumeric():
                twofa = None
        return login, password, twofa