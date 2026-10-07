/**
 * Chat history (every route needs a session):
 * - `POST /api/conversations` `{link_id}`: start a conversation about one of the person's ready
 *   repositories; 409 at 50 conversations (an old one must be deleted first: history is never
 *   deleted on the person's behalf)
 * - `GET /api/conversations`: the person's conversations, most recently used first
 * - `GET /api/conversations/:id`: one conversation with its messages, oldest first
 * - `POST /api/conversations/:id/messages` `{question, exclude_tests?}`: ask; the whole reply is
 *   saved and returned (201), and the first question becomes the title. The repository must still
 *   be linked and ready (409 otherwise; the history stays readable), and a conversation holds at
 *   most 100 messages (409). A refusal from the service (a used-up quota as 503 with
 *   `Retry-After`, an answer that failed its citation check as 502, a version that cannot be
 *   searched as 409) passes through and saves nothing
 * - `DELETE /api/conversations/:id`: delete the conversation and its messages
 *
 * Asking also uses one of the person's 5 questions a minute (in memory) and 30 a day (in the
 * database), 429 with `Retry-After` when used up; both are given back when the service refused
 * before Gemini did any work, but not when an answer failed its citation check, since Gemini did
 * the work. The conversation and message counts are checked before asking, so two questions sent
 * at the same moment can take a conversation one or two past 100; nothing depends on the exact
 * number.
 */

const express = require("express");

const { ApiError } = require("./apiError");
const { LinkStatus } = require("./repositoryLinks");
const { booleanField, requireFields, textField } = require("./requestFields");
const { refusedBeforeGemini } = require("./retrievalService");
const { UsageKind } = require("./usage");

const CREATED_STATUS = 201;

const NO_CONTENT_STATUS = 204;

const NOT_FOUND_STATUS = 404;

const CONFLICT_STATUS = 409;

const TOO_MANY_REQUESTS_STATUS = 429;

const MAX_TITLE_CHARACTERS = 80;

const TITLE_CUT_MARK = "…";

const UNKNOWN_CONVERSATION_MESSAGE = "No such conversation";

const UNKNOWN_LINK_MESSAGE = "No such repository";

const NOT_READY_MESSAGE = "This repository is not ready for questions yet";

const NOT_LINKED_MESSAGE = "Add this repository again before asking about it";

const TOO_MANY_QUESTIONS_THIS_MINUTE_MESSAGE =
  "You are asking questions quickly; wait a moment before the next one";

/**
 * @param {{
 *   retrievalService: ReturnType<typeof import("./retrievalService").createRetrievalService>,
 *   repositoryLinks: ReturnType<typeof import("./repositoryLinks").createMongoRepositoryLinkStore>,
 *   conversations: ReturnType<typeof import("./conversations").createMongoConversationStore>,
 *   dailyLimits: ReturnType<typeof import("./usage").createDailyLimits>,
 *   questionsPerMinute: ReturnType<typeof import("./slidingWindowLimiter").createSlidingWindowLimiter>,
 *   limits: typeof import("./limits").DEFAULT_LIMITS,
 *   requireUser: import("express").RequestHandler,
 *   now?: () => number,
 * }} dependencies
 */
function createConversationRouter({
  retrievalService,
  repositoryLinks,
  conversations,
  dailyLimits,
  questionsPerMinute,
  limits,
  requireUser,
  now = Date.now,
}) {
  const router = express.Router();
  router.use(requireUser);

  router.post("/", async (request, response) => {
    const { link_id: linkId } = requireFields(request.body, ["link_id"]);
    const link = await repositoryLinks.findForUser(request.user.id, linkId);
    if (link === null) {
      throw new ApiError(NOT_FOUND_STATUS, UNKNOWN_LINK_MESSAGE);
    }
    if (link.status !== LinkStatus.READY) {
      throw new ApiError(CONFLICT_STATUS, NOT_READY_MESSAGE);
    }
    if ((await conversations.countForUser(request.user.id)) >= limits.conversationsPerUser) {
      throw new ApiError(CONFLICT_STATUS, tooManyConversationsMessage(limits.conversationsPerUser));
    }
    const conversation = await conversations.create({
      userId: request.user.id,
      repository: link.repository,
      version: link.version,
      now: new Date(now()),
    });
    response.status(CREATED_STATUS).json(publicConversation(conversation));
  });

  router.get("/", async (request, response) => {
    const listed = await conversations.listForUser(request.user.id);
    response.json({ conversations: listed.map(publicConversation) });
  });

  router.get("/:conversationId", async (request, response) => {
    const conversation = await findOwnedConversation(request);
    const messages = await conversations.listMessages(conversation.id);
    response.json({ ...publicConversation(conversation), messages: messages.map(publicMessage) });
  });

  router.post("/:conversationId/messages", async (request, response) => {
    const { question, excludeTests } = readQuestion(request.body);
    const conversation = await findOwnedConversation(request);
    await requireReadyLink(request.user.id, conversation);
    if ((await conversations.countMessages(conversation.id)) >= limits.messagesPerConversation) {
      throw new ApiError(CONFLICT_STATUS, conversationFullMessage(limits.messagesPerConversation));
    }
    const slots = await takeQuestionSlots(request.user.id);
    let reply;
    try {
      reply = await retrievalService.ask({
        repository: conversation.repository,
        version: conversation.version,
        question,
        excludeTests,
      });
    } catch (error) {
      if (refusedBeforeGemini(error)) {
        await giveBackQuestionSlots(slots);
      }
      throw error;
    }
    const message = await conversations.addMessage({
      userId: request.user.id,
      conversationId: conversation.id,
      question,
      excludeTests,
      reply,
      title: titleFor(question),
      now: new Date(now()),
    });
    response.status(CREATED_STATUS).json(publicMessage(message));
  });

  router.delete("/:conversationId", async (request, response) => {
    const removed = await conversations.remove(request.user.id, request.params.conversationId);
    if (!removed) {
      throw new ApiError(NOT_FOUND_STATUS, UNKNOWN_CONVERSATION_MESSAGE);
    }
    response.status(NO_CONTENT_STATUS).end();
  });

  /**
   * Take a slot from this minute's questions (in memory), then from today's (in the database);
   * if today's is used up, this minute's slot is given back.
   * @param {string} userId
   */
  async function takeQuestionSlots(userId) {
    const minute = questionsPerMinute.take(userId);
    if (!minute.taken) {
      throw new ApiError(
        TOO_MANY_REQUESTS_STATUS,
        TOO_MANY_QUESTIONS_THIS_MINUTE_MESSAGE,
        minute.retryAfterSeconds,
      );
    }
    try {
      const day = await dailyLimits.take(userId, UsageKind.QUESTIONS);
      return { userId, minuteTime: minute.time, day };
    } catch (error) {
      questionsPerMinute.giveBack(userId, minute.time);
      throw error;
    }
  }

  /** @param {{ userId: string, minuteTime: number, day: object }} slots */
  async function giveBackQuestionSlots(slots) {
    questionsPerMinute.giveBack(slots.userId, slots.minuteTime);
    await dailyLimits.giveBack(slots.day);
  }

  /** @param {import("express").Request} request */
  async function findOwnedConversation(request) {
    const conversation = await conversations.findForUser(
      request.user.id,
      request.params.conversationId,
    );
    if (conversation === null) {
      throw new ApiError(NOT_FOUND_STATUS, UNKNOWN_CONVERSATION_MESSAGE);
    }
    return conversation;
  }

  /**
   * @param {string} userId
   * @param {import("./conversations").Conversation} conversation
   */
  async function requireReadyLink(userId, conversation) {
    const link = await repositoryLinks.findByTarget(
      userId,
      conversation.repository,
      conversation.version,
    );
    if (link === null) {
      throw new ApiError(CONFLICT_STATUS, NOT_LINKED_MESSAGE);
    }
    if (link.status !== LinkStatus.READY) {
      throw new ApiError(CONFLICT_STATUS, NOT_READY_MESSAGE);
    }
  }

  return router;
}

/**
 * @param {unknown} body
 * @returns {{ question: string, excludeTests: boolean }}
 */
function readQuestion(body) {
  const fields = requireFields(body, ["question"], ["exclude_tests"]);
  const question = textField(fields.question, "question");
  const excludeTests =
    "exclude_tests" in fields ? booleanField(fields.exclude_tests, "exclude_tests") : false;
  return { question, excludeTests };
}

/** @param {number} limit */
function tooManyConversationsMessage(limit) {
  return `You have ${limit} conversations; delete one before starting another`;
}

/** @param {number} limit */
function conversationFullMessage(limit) {
  return `This conversation has ${limit} messages; start a new one`;
}

/**
 * The first 80 characters of the question, counted as people see them (an emoji is one).
 * @param {string} question
 */
function titleFor(question) {
  const characters = Array.from(question);
  if (characters.length <= MAX_TITLE_CHARACTERS) {
    return question;
  }
  return characters.slice(0, MAX_TITLE_CHARACTERS - 1).join("").trimEnd() + TITLE_CUT_MARK;
}

/** @param {import("./conversations").Conversation} conversation */
function publicConversation(conversation) {
  return {
    id: conversation.id,
    repository: conversation.repository,
    version: conversation.version,
    title: conversation.title,
    created_at: conversation.createdAt.toISOString(),
    updated_at: conversation.updatedAt.toISOString(),
  };
}

/** @param {import("./conversations").Message} message */
function publicMessage(message) {
  return {
    id: message.id,
    question: message.question,
    exclude_tests: message.excludeTests,
    reply: message.reply,
    created_at: message.createdAt.toISOString(),
  };
}

module.exports = {
  NOT_LINKED_MESSAGE,
  NOT_READY_MESSAGE,
  TOO_MANY_QUESTIONS_THIS_MINUTE_MESSAGE,
  UNKNOWN_CONVERSATION_MESSAGE,
  UNKNOWN_LINK_MESSAGE,
  conversationFullMessage,
  createConversationRouter,
  titleFor,
  tooManyConversationsMessage,
};
