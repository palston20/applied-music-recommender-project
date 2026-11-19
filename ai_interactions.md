# AI Interactions Log

> **Stretch features only.** Only fill in the sections that apply to stretch features you attempted. If you did not attempt a stretch feature, leave its section blank or delete it. This file is not required for the core project.

---

## Agentic Workflow (SF8)

**What did you verify or fix manually?***

The most useful thing I did was test the application myself instead of just relying on the test results. When I requested a hyperpop playlist, I noticed that I only received one experimental song while most of the other recommendations were hip hop, even though all the evaluation checks passed. This made me realize that my dataset didn't have enough songs to support certain requests and that my evaluator wasn't checking whether the recommendations actually matched the genres I requested. I brought this issue to Claude and asked for help improving the genre-ranking logic and adding a new evaluation check. I also made sure to keep the hyperpop request as a regression test so I could catch similar issues in the future.

I also decided to replace the original 18 fictional songs with real music. Although the original songs were useful for testing, I wanted Soundtrack AI to feel like something people could actually use and enjoy. I asked Claude to help update the catalog while keeping my existing recommendation algorithm. After switching to real songs, the relevance score increased from 63 to 90 because there were more options to match different moods and genres. This experience also made me realize how important the dataset is to the recommendation algorithm. Since the energy and mood values are estimated manually, they directly affect how songs are ranked.

Another challenge came when I connected my Gemini API key. Even though my mocked tests were passing, my first actual API call got stuck because there was no timeout and the model was overloaded. I worked with Claude to troubleshoot the issue and update the API configuration. This taught me that passing tests with mocked responses doesn't necessarily mean the actual application will work. I also noticed that Gemini sometimes generated descriptions that sounded convincing but weren't accurate. For example, it described Mr. Brightside as having "swapped guitars for glitter." This made me realize that checking whether a description mentions a song isn't enough to verify that the information is actually correct.

I also learned to question Claude's suggestions and verify its claims instead of automatically accepting them. At one point, it suggested that the evaluation clearly showed the difference between a more advanced interpreter and a basic one. However, after a bug in the tests was fixed, the simpler keyword-based version passed everything as well, so I decided that claim shouldn't be included. I also caught a playlist title in the README that came from a different run and made sure it was replaced with actual application output. Overall, I learned that AI is useful for helping with implementation and debugging, but I still need to understand the code, test the results myself, and make the final decisions about what belongs in my project.

**Issues the evaluation exposed during the session:**

- The first discovery recommendations were weak (a country song appeared in a gym playlist). I reviewed this result and asked Claude to help improve the discovery ranking by introducing a minimum vibe score.
- The offline heuristic ignored negation: "nothing upbeat" became *happy*, and "no country" still returned country. I helped identify this issue, and we worked on adding negation handling and a constraint check.
- My first live Gemini call got stuck because there was no timeout and the default model was overloaded (503). I worked with Claude to troubleshoot the API configuration and add a 20-second timeout.
- Testing revealed a bug in the evaluation script itself: three cases expected five tracks even though the requests never specified a playlist length. I reviewed


There are still limitations I would like to address in future versions of Soundtrack AI. The current music catalog limits the variety of songs available, and the manually assigned mood and energy values can affect recommendation accuracy. I would like to expand the catalog, improve how song attributes are validated, and develop a better way to verify that AI-generated playlist descriptions accurately reflect each song. I would also like to continue testing with real users to see whether the playlists actually match the vibes they describe.