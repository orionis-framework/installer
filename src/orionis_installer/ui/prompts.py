"""Collect prompt-toolkit input without executing installation callbacks."""

from collections.abc import Callable

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from prompt_toolkit.validation import ValidationError as PromptValidationError
from prompt_toolkit.validation import Validator

from orionis_installer.exceptions import InstallerError
from orionis_installer.ui.messages import MESSAGES
from orionis_installer.ui.theme import terminal_text

PROMPT_STYLE = Style.from_dict(
    {"question": "bold ansicyan", "selected": "bold ansimagenta", "hint": "ansibrightblack"}
)


class InputValidator(Validator):
    """Adapt pure application validation to prompt-toolkit input errors."""

    def __init__(self, callback: Callable[[str], object]) -> None:
        """Store the pure validation callback used for entered text.

        Parameters
        ----------
        callback : Callable
            Function that accepts text or raises a validation-related exception.
        """
        self.callback = callback

    def validate(self, document: Document) -> None:
        """Validate input and position any failure at the end of the text.

        Parameters
        ----------
        document : Document
            Current prompt-toolkit input buffer.

        Raises
        ------
        PromptValidationError
            If the callback rejects input with InstallerError or ValueError.
        """
        try:
            self.callback(document.text)
        except (InstallerError, ValueError) as exc:
            raise PromptValidationError(
                message=terminal_text(exc), cursor_position=len(document.text)
            ) from exc


class Prompts:
    """Provide validated text fields and keyboard-operated choices."""

    def __init__(self, *, no_color: bool = False) -> None:
        """Store the color preference used by each prompt application.

        Parameters
        ----------
        no_color : bool, optional
            Whether prompts should use monochrome terminal rendering.
        """
        self.no_color = no_color

    @property
    def color_depth(self) -> ColorDepth:
        """Choose the terminal color depth for the configured preference.

        Returns
        -------
        ColorDepth
            Monochrome depth without color, or the standard ANSI palette depth.
        """
        return ColorDepth.DEPTH_1_BIT if self.no_color else ColorDepth.DEPTH_4_BIT

    def text(
        self,
        label: str,
        default: str = "",
        validator: Callable[[str], object] | None = None,
        password: bool = False,
    ) -> str:
        """Read editable text with immediate validation and optional masking.

        Parameters
        ----------
        label : str
            Question displayed before the editable field.
        default : str, optional
            Initial editable text.
        validator : Callable or None, optional
            Pure callback that validates entered text before acceptance.
        password : bool, optional
            Whether to hide entered characters.

        Returns
        -------
        str
            Accepted text from the prompt session.

        Raises
        ------
        KeyboardInterrupt
            If the user cancels the input session.
        EOFError
            If input closes before a value is accepted.
        """
        session: PromptSession[str] = PromptSession(
            style=PROMPT_STYLE, color_depth=self.color_depth
        )
        return session.prompt(
            FormattedText([("class:question", terminal_text(label) + ": ")]),
            default=default,
            validator=InputValidator(validator) if validator else None,
            validate_while_typing=True,
            is_password=password,
        )

    def select(self, label: str, choices: list[tuple[str, str]], default: str) -> str:
        """Accept a keyboard-selected value while restoring the terminal afterward.

        Parameters
        ----------
        label : str
            Question displayed above the choices.
        choices : list of tuple of str
            Ordered machine values and visible captions.
        default : str
            Machine value initially selected and visibly marked as the default.

        Returns
        -------
        str
            Machine value accepted with Enter.

        Raises
        ------
        ValueError
            If choices are empty or omit the default value.
        KeyboardInterrupt
            If the user presses Ctrl+C.
        EOFError
            If the user presses Ctrl+D.
        """
        values = [value for value, _ in choices]
        if not values or default not in values:
            raise ValueError(MESSAGES["invalid_selector"])
        selected = values.index(default)
        bindings = KeyBindings()

        @bindings.add("up")
        @bindings.add("k")
        def previous(event: KeyPressEvent) -> None:
            """Move selection to the preceding choice with cyclic navigation.

            Parameters
            ----------
            event : KeyPressEvent
                Key event whose application must redraw the updated selection.
            """
            nonlocal selected
            selected = (selected - 1) % len(values)
            event.app.invalidate()

        @bindings.add("down")
        @bindings.add("j")
        @bindings.add("tab")
        def following(event: KeyPressEvent) -> None:
            """Move selection to the following choice with cyclic navigation.

            Parameters
            ----------
            event : KeyPressEvent
                Key event whose application must redraw the updated selection.
            """
            nonlocal selected
            selected = (selected + 1) % len(values)
            event.app.invalidate()

        @bindings.add("enter")
        def accept(event: KeyPressEvent) -> None:
            """Finish the selector with the currently selected machine value.

            Parameters
            ----------
            event : KeyPressEvent
                Enter key event providing the selector application.
            """
            event.app.exit(result=values[selected])

        @bindings.add("c-c")
        def cancel(event: KeyPressEvent) -> None:
            """Finish the selector with a user cancellation exception.

            Parameters
            ----------
            event : KeyPressEvent
                Ctrl+C event providing the selector application.
            """
            event.app.exit(exception=KeyboardInterrupt())

        @bindings.add("c-d")
        def eof(event: KeyPressEvent) -> None:
            """Finish the selector with an end-of-input exception.

            Parameters
            ----------
            event : KeyPressEvent
                Ctrl+D event providing the selector application.
            """
            event.app.exit(exception=EOFError())

        def render() -> FormattedText:
            """Render current selection markers and the visible default caption.

            Returns
            -------
            FormattedText
                Styled choice lines for the current selection state.
            """
            lines: list[tuple[str, str]] = []
            for index, (_, caption) in enumerate(choices):
                marker = "> " if index == selected else "  "
                hint = MESSAGES["default_marker"] if values[index] == default else ""
                lines.append(
                    (
                        "class:selected" if index == selected else "",
                        marker + terminal_text(caption) + hint + "\n",
                    )
                )
            return FormattedText(lines)

        layout = Layout(
            HSplit(
                [
                    Window(
                        FormattedTextControl(
                            FormattedText([("class:question", terminal_text(label))])
                        ),
                        height=1,
                    ),
                    Window(FormattedTextControl(render), height=len(choices)),
                    Window(
                        FormattedTextControl(
                            FormattedText([("class:hint", MESSAGES["navigation"])])
                        ),
                        height=1,
                    ),
                ]
            )
        )
        application: Application[str] = Application(
            layout=layout,
            key_bindings=bindings,
            style=PROMPT_STYLE,
            color_depth=self.color_depth,
            full_screen=False,
            erase_when_done=True,
        )
        return application.run()

    def confirm(self, label: str, default: bool) -> bool:
        """Convert a yes-or-no selection into an operation consent decision.

        Parameters
        ----------
        label : str
            Consent question displayed above the choices.
        default : bool
            Initial decision proposed to the user.

        Returns
        -------
        bool
            Whether the accepted choice permits the operation.

        Raises
        ------
        KeyboardInterrupt
            If the user cancels the selector.
        EOFError
            If the user closes input without selecting a decision.
        """
        return (
            self.select(
                label,
                [("yes", MESSAGES["yes"]), ("no", MESSAGES["no"])],
                "yes" if default else "no",
            )
            == "yes"
        )
