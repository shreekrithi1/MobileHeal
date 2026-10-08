package com.mobileheal.domain.error

/** Typed failures surfaced to the presentation layer. Data-layer exceptions are mapped onto these. */
sealed class DomainError(message: String, cause: Throwable? = null) : Exception(message, cause) {
    class Network(cause: Throwable? = null) : DomainError("Can't reach the server. Check your connection.", cause)
    class Server(val code: Int, detail: String) : DomainError("Server error $code: $detail")
    class NotFound(what: String) : DomainError("$what not found")
    class Unexpected(cause: Throwable? = null) : DomainError("Something went wrong", cause)
}
