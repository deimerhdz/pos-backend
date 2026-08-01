from pydantic import BaseModel, Field


class TicketResponse(BaseModel):
    """Ticket de un solo uso para abrir el stream desde el navegador del staff."""

    ticket: str = Field(description="Se consume al abrir el stream; no se reutiliza")
    expires_in: int = Field(description="Segundos de vida del ticket")
